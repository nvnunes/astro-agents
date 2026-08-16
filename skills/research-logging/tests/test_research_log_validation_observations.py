from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_validation_test_support import write

OBSERVATIONS = importlib.import_module("validation.observations")


def cached_identity(path: Path, sha256: str) -> dict[str, object]:
    metadata = path.stat()
    return {
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
        "sha256": sha256,
    }


class ObservationTests(unittest.TestCase):
    def test_unchanged_metadata_opens_and_hashes_zero_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.csv"
            write(path, "value\n")
            cached = cached_identity(path, "a" * 64)
            session = OBSERVATIONS.ObservationSession()
            with mock.patch.object(Path, "open", side_effect=AssertionError("opened")):
                observed = session.observe(path, cached)

            self.assertEqual(observed.status, OBSERVATIONS.METADATA_UNCHANGED)
            self.assertEqual(
                session.diagnostics.as_dict(),
                {
                    "metadata_checked": 1,
                    "hashes_reused": 1,
                    "files_hashed": 0,
                    "bytes_hashed": 0,
                    "content_changed": 0,
                },
            )

    def test_metadata_rewrite_with_same_content_hashes_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.csv"
            write(path, "value\n")
            session = OBSERVATIONS.ObservationSession()
            initial = session.observe(path).identity
            assert initial is not None
            os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1))
            second = OBSERVATIONS.ObservationSession()
            observed = second.observe(path, initial)
            again = second.observe(path, initial)

            self.assertEqual(observed.status, OBSERVATIONS.CONTENT_UNCHANGED)
            self.assertIs(again, observed)
            self.assertEqual(second.diagnostics.files_hashed, 1)
            self.assertEqual(second.diagnostics.bytes_hashed, len("value\n"))

    def test_changed_content_hashes_once_and_reopens_only_dependents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changed = root / "changed.csv"
            stable = root / "stable.csv"
            write(changed, "old\n")
            write(stable, "stable\n")
            seed = OBSERVATIONS.ObservationSession()
            changed_identity = seed.observe(changed).identity
            stable_identity = seed.observe(stable).identity
            assert changed_identity is not None and stable_identity is not None
            write(changed, "new content\n")
            session = OBSERVATIONS.ObservationSession()
            changed_observation = session.observe(changed, changed_identity)
            stable_observation = session.observe(stable, stable_identity)
            outcomes = [
                {
                    "compatibility_identity": "changed-outcome",
                    "dependencies": [
                        {"path": changed.as_posix(), "identity": changed_identity}
                    ],
                },
                {
                    "compatibility_identity": "stable-outcome",
                    "dependencies": [
                        {"path": stable.as_posix(), "identity": stable_identity}
                    ],
                },
            ]
            retained, reopened = OBSERVATIONS.retain_compatible_outcomes(
                outcomes,
                {
                    changed.as_posix(): changed_observation,
                    stable.as_posix(): stable_observation,
                },
            )

            self.assertEqual(changed_observation.status, OBSERVATIONS.CONTENT_CHANGED)
            self.assertEqual(session.diagnostics.files_hashed, 1)
            self.assertEqual(
                [row["compatibility_identity"] for row in retained],
                ["stable-outcome"],
            )
            self.assertEqual(
                [row["compatibility_identity"] for row in reopened],
                ["changed-outcome"],
            )

    def test_multiple_consumers_share_observation_and_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.csv"
            write(path, "value\n")
            session = OBSERVATIONS.ObservationSession()
            calls = 0

            def inspect(source: Path) -> str:
                nonlocal calls
                calls += 1
                return source.read_text(encoding="utf-8")

            first = session.inspect(path, inspect)
            second = session.inspect(path, inspect)
            self.assertEqual(first[1], "value\n")
            self.assertEqual(second[1], "value\n")
            self.assertEqual(calls, 1)
            self.assertEqual(session.diagnostics.files_hashed, 1)

    def test_target_outcomes_share_one_cached_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "evidence.csv"
            write(path, "value\n")
            seed = OBSERVATIONS.ObservationSession()
            identity = seed.observe(path).identity
            assert identity is not None
            outcomes = [
                {
                    "dependencies": [
                        {"path": "evidence.csv", "identity": identity}
                    ]
                },
                {
                    "dependencies": [
                        {"path": "evidence.csv", "identity": identity}
                    ]
                },
            ]
            session = OBSERVATIONS.ObservationSession()
            observed = OBSERVATIONS.observe_outcome_dependencies(
                session, outcomes, {"evidence.csv": identity}, root
            )

            self.assertEqual(set(observed), {"evidence.csv"})
            self.assertEqual(session.diagnostics.metadata_checked, 1)
            self.assertEqual(session.diagnostics.files_hashed, 0)

    def test_metadata_only_change_retains_and_updates_outcome_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.csv"
            write(path, "value\n")
            seed = OBSERVATIONS.ObservationSession()
            identity = seed.observe(path).identity
            assert identity is not None
            os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1))
            session = OBSERVATIONS.ObservationSession()
            observation = session.observe(path, identity)
            retained, reopened = OBSERVATIONS.retain_compatible_outcomes(
                [
                    {
                        "compatibility_identity": "outcome",
                        "dependencies": [
                            {"path": path.as_posix(), "identity": identity}
                        ],
                    }
                ],
                {path.as_posix(): observation},
            )
            self.assertEqual(len(retained), 1)
            self.assertFalse(reopened)
            self.assertEqual(
                retained[0]["dependencies"][0]["identity"], observation.identity
            )

    def test_missing_inaccessible_ambiguous_and_mid_read_change_are_explicit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = OBSERVATIONS.ObservationSession().observe(root / "missing")
            ambiguous = OBSERVATIONS.ObservationSession().observe(root)
            path = root / "changing.csv"
            write(path, "value\n")
            session = OBSERVATIONS.ObservationSession()
            original_stat = session._stat
            calls = 0

            def changed_stat(source: Path):
                nonlocal calls
                calls += 1
                value = original_stat(source)
                if calls == 2:
                    write(source, "changed\n")
                    value = original_stat(source)
                return value

            with mock.patch.object(session, "_stat", side_effect=changed_stat):
                changing = session.observe(path)
            inaccessible_session = OBSERVATIONS.ObservationSession()
            with mock.patch.object(
                inaccessible_session,
                "_stat",
                side_effect=PermissionError("denied"),
            ):
                inaccessible = inaccessible_session.observe(path)

            self.assertEqual(missing.status, OBSERVATIONS.MISSING)
            self.assertEqual(ambiguous.status, OBSERVATIONS.AMBIGUOUS)
            self.assertEqual(inaccessible.status, OBSERVATIONS.INACCESSIBLE)
            self.assertEqual(
                changing.status, OBSERVATIONS.CHANGED_DURING_OBSERVATION
            )
            for observation in (missing, ambiguous, inaccessible, changing):
                self.assertFalse(observation.resolved)


if __name__ == "__main__":
    unittest.main()
