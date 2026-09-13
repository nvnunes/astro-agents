from __future__ import annotations

import io
import threading
import time
import unittest

from stream_capture import StreamCapture, StreamDestination


class _FailingDestination(io.BytesIO):
    def write(self, value: bytes) -> int:
        raise OSError("destination unavailable")


class _UndurableDestination(io.BytesIO):
    def fileno(self) -> int:
        raise OSError("durable flush unavailable")


class _BlockingSource:
    def __init__(self) -> None:
        self.closed = False
        self._released = threading.Event()

    def read(self, size: int) -> bytes:
        del size
        self._released.wait(timeout=1)
        return b""

    def close(self) -> None:
        self.closed = True
        self._released.set()


class StreamCaptureTests(unittest.TestCase):
    def test_failed_destination_does_not_stop_other_destination_or_drain(self) -> None:
        source = io.BytesIO(b"retained bytes")
        retained = io.BytesIO()
        capture = StreamCapture(chunk_bytes=3)

        capture.start(
            source,
            (
                StreamDestination("required", _FailingDestination(), True),
                StreamDestination("mirror", retained, False),
            ),
        )
        failures = capture.finish(1)

        self.assertTrue(source.closed)
        self.assertEqual(retained.getvalue(), b"retained bytes")
        self.assertTrue(capture.required_failed.is_set())
        self.assertEqual(
            [(value.name, value.required) for value in failures],
            [("required", True)],
        )

    def test_optional_destination_failure_does_not_mark_capture_required(self) -> None:
        retained = io.BytesIO()
        capture = StreamCapture()

        capture.start(
            io.BytesIO(b"complete"),
            (
                StreamDestination("capture", retained, True),
                StreamDestination("mirror", _FailingDestination(), False),
            ),
        )
        failures = capture.finish(1)

        self.assertEqual(retained.getvalue(), b"complete")
        self.assertFalse(capture.required_failed.is_set())
        self.assertEqual(
            [(value.name, value.required) for value in failures],
            [("mirror", False)],
        )

    def test_durable_flush_failure_is_required(self) -> None:
        capture = StreamCapture()
        capture.start(
            io.BytesIO(b"complete"),
            (
                StreamDestination(
                    "capture", _UndurableDestination(), True, durable=True
                ),
            ),
        )

        failures = capture.finish(1)

        self.assertTrue(capture.required_failed.is_set())
        self.assertEqual(str(failures[0].error), "durable flush unavailable")

    def test_finish_uses_one_bound_and_closes_a_stuck_source(self) -> None:
        source = _BlockingSource()
        capture = StreamCapture()
        capture.start(
            source,  # type: ignore[arg-type]
            (StreamDestination("capture", io.BytesIO(), True),),
        )
        started = time.monotonic()

        failures = capture.finish(0.01)

        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(source.closed)
        self.assertTrue(capture.required_failed.is_set())
        self.assertIn("did not close", str(failures[0].error))


if __name__ == "__main__":
    unittest.main()
