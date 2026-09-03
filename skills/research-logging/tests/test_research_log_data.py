from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIRECTORY = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import research_log_data as DATA  # noqa: E402
from research_log_validation_test_support import write  # noqa: E402
from validation import filesystem as FILESYSTEM  # noqa: E402
from validation import retention as RETENTION  # noqa: E402


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def data_fixture(root: Path) -> tuple[Path, Path]:
    entry = root / "docs" / "study" / "entries" / "2026-09-01-e001-study"
    source = entry / "data" / "source.csv"
    write(source, "value\n1\n")
    digest = file_digest(source)
    write(
        entry / "data.json",
        json.dumps(
            {
                "schema": DATA.DATA_SCHEMA,
                "inputs": [
                    {
                        "name": "source",
                        "kind": "file",
                        "location": "data/source.csv",
                        "fingerprint": {"algorithm": "sha256", "digest": digest},
                        "origin": True,
                    }
                ],
            },
            indent=2,
        )
        + "\n",
    )
    return entry, source


class DataFileTests(unittest.TestCase):
    def test_input_token_parts_enforces_complete_member_syntax(self) -> None:
        self.assertEqual(DATA.input_token_parts("<results>"), ("results", None))
        self.assertEqual(
            DATA.input_token_parts("<results>/nested/file.csv"),
            ("results", "nested/file.csv"),
        )
        for token in (
            "<results>/../secret.csv",
            "<results>/nested//file.csv",
            "<results>/nested\\file.csv",
            "<results>/https://host/file.csv",
        ):
            with self.subTest(token=token):
                self.assertIsNone(DATA.input_token_parts(token))

    def test_retired_v1_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry, _ = data_fixture(Path(directory))
            path = entry / "data.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["schema"] = "research-log-data/v1"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                DATA.DataContractError, "data.declaration.invalid"
            ):
                DATA.load_data_file(path, entry_root=entry)

    def test_numeric_entry_family_input_names_are_reserved(self) -> None:
        for name in ("e004", "E004"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                entry, _ = data_fixture(Path(directory))
                path = entry / "data.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["inputs"][0]["name"] = name
                path.write_text(json.dumps(payload), encoding="utf-8")

                with self.assertRaisesRegex(
                    DATA.DataContractError, "data.declaration.invalid"
                ):
                    DATA.load_data_file(path, entry_root=entry)

    def test_strict_file_decodes_and_serializes_canonically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry, source = data_fixture(Path(directory))

            data_file = DATA.load_data_file(entry / "data.json", entry_root=entry)

            self.assertEqual(data_file.inputs[0].name, "source")
            self.assertEqual(
                data_file.inputs[0].canonical_target, str(source.resolve())
            )
            self.assertEqual(len(data_file.identity), 64)
            self.assertEqual(
                json.loads(data_file.canonical_json())["schema"], DATA.DATA_SCHEMA
            )
            DATA.verify_fingerprint(data_file.inputs[0])

    def test_oversized_data_file_is_rejected_before_whole_file_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry, _ = data_fixture(Path(directory))
            with (
                mock.patch.object(DATA, "MAX_DATA_FILE_BYTES", 4),
                mock.patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError("whole-file read is forbidden"),
                ),
            ):
                with self.assertRaisesRegex(
                    DATA.DataContractError, "data.declaration.invalid"
                ):
                    DATA.load_data_file(entry / "data.json", entry_root=entry)

    def test_duplicate_keys_names_and_targets_fail_precisely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry, _ = data_fixture(Path(directory))
            path = entry / "data.json"
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace('"schema":', '"schema":"x","schema":', 1))
            with self.assertRaisesRegex(
                DATA.DataContractError, "data.declaration.invalid"
            ):
                DATA.load_data_file(path, entry_root=entry)

        with tempfile.TemporaryDirectory() as directory:
            entry, _ = data_fixture(Path(directory))
            path = entry / "data.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["inputs"].append(dict(payload["inputs"][0]))
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(DATA.DataContractError, "data.name.duplicate"):
                DATA.load_data_file(path, entry_root=entry)

        with tempfile.TemporaryDirectory() as directory:
            entry, _ = data_fixture(Path(directory))
            path = entry / "data.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            alias = dict(payload["inputs"][0])
            alias["name"] = "alias"
            payload["inputs"].append(alias)
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                DATA.DataContractError, "data.target.duplicate"
            ):
                DATA.load_data_file(path, entry_root=entry)

    def test_remote_locations_and_immutable_fingerprints_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry, _ = data_fixture(Path(directory))
            path = entry / "data.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            remote = {
                "name": "archive",
                "kind": "file",
                "location": "s3://archive/catalog.csv?versionId=v2",
                "fingerprint": {"algorithm": "immutable-source", "value": "v2"},
                "origin": True,
            }
            payload["inputs"] = [remote]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                DATA.DataContractError, "data.declaration.invalid"
            ):
                DATA.load_data_file(path, entry_root=entry)

            payload["inputs"][0] = {
                **remote,
                "location": "data//source.csv",
                "fingerprint": {
                    "algorithm": "sha256",
                    "digest": "0" * 64,
                },
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                DATA.DataContractError, "data.declaration.invalid"
            ):
                DATA.load_data_file(path, entry_root=entry)

    def test_shared_artifact_roots_cannot_be_declared_as_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry, _ = data_fixture(Path(directory))
            path = entry / "data.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            locations = ("data", "images", "data/../data", "images/../images")
            for index, location in enumerate(locations):
                with self.subTest(location=location):
                    payload["inputs"] = [
                        {
                            "name": f"artifact-root-{index}",
                            "kind": "directory",
                            "location": location,
                            "fingerprint": {
                                "algorithm": "directory-sha256-v1",
                                "digest": "0" * 64,
                            },
                            "origin": True,
                        }
                    ]
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(
                        DATA.DataContractError, "data.declaration.invalid"
                    ):
                        DATA.load_data_file(path, entry_root=entry)

    def test_relative_parent_path_outside_entry_is_normalized_before_symlink_check(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry, _ = data_fixture(root)
            source = root / "shared" / "source.csv"
            write(source, "value\n1\n")
            location = Path(os.path.relpath(source, entry)).as_posix()

            resource = DATA.build_local_input(
                "shared-source", "file", location, entry_root=entry
            )
            data_file = DATA.data_file_from_inputs(
                entry / "data.json", entry_root=entry, inputs=(resource,)
            )

            self.assertEqual(
                data_file.inputs[0].canonical_target, str(source.resolve())
            )

    def test_external_input_rejects_a_lexical_directory_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry, _ = data_fixture(root)
            retained = root / "retained"
            write(retained / "source.csv", "value\n1\n")
            alias = root / "alias"
            alias.symlink_to(retained, target_is_directory=True)
            location = Path(os.path.relpath(alias / "source.csv", entry)).as_posix()

            with self.assertRaisesRegex(
                DATA.DataContractError, "data.declaration.invalid"
            ):
                DATA.build_local_input(
                    "aliased-source", "file", location, entry_root=entry
                )

    def test_fingerprint_drift_is_never_silently_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry, source = data_fixture(Path(directory))
            resource = DATA.load_data_file(
                entry / "data.json", entry_root=entry
            ).inputs[0]
            source.write_text("value\n2\n", encoding="utf-8")

            with self.assertRaisesRegex(
                DATA.DataContractError, "data.fingerprint.mismatch"
            ):
                DATA.verify_fingerprint(resource)

    def test_file_fingerprint_cache_reuses_only_exact_current_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry, _ = data_fixture(Path(directory))
            resource = DATA.load_data_file(
                entry / "data.json", entry_root=entry
            ).inputs[0]
            observed = DATA.verify_fingerprint(resource)
            assert observed is not None
            cached = DATA.fingerprint_observation_record(resource, observed)

            with mock.patch.object(
                DATA,
                "observe_fingerprint",
                side_effect=AssertionError("content must not be rehashed"),
            ):
                reused = DATA.verify_fingerprint(resource, cached=cached)

            assert reused is not None
            self.assertTrue(reused.identity_reused)

    def test_directory_fingerprint_cache_preserves_verified_membership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            collection = entry / "collection"
            write(collection / "a.txt", "a")
            resource = DATA.build_local_input(
                "collection", "directory", "collection", entry_root=entry
            )
            observed = DATA.verify_fingerprint(resource)
            assert observed is not None
            cached = DATA.fingerprint_observation_record(resource, observed)

            with mock.patch.object(
                DATA,
                "observe_fingerprint",
                side_effect=AssertionError("content must not be rehashed"),
            ):
                reused = DATA.verify_fingerprint(resource, cached=cached)

            assert reused is not None
            self.assertTrue(reused.identity_reused)
            self.assertEqual([item.path for item in reused.entries], ["a.txt"])

    def test_identity_files_define_a_bounded_managed_directory_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            build = entry / "build"
            write(build / "build.h5", "state")
            write(build / "build.yaml", "mode: test\n")
            write(build / "products" / "outer.h5", "large product")

            resource = DATA.build_identity_directory(
                "build",
                "build",
                ("build.yaml", "build.h5"),
                entry_root=entry,
            )
            baseline = DATA.verify_fingerprint(resource)
            assert baseline is not None

            self.assertEqual(resource.fingerprint.files, ("build.h5", "build.yaml"))
            self.assertEqual(
                resource.fingerprint.as_dict()["algorithm"],
                "identity-files-sha256-v1",
            )
            self.assertEqual(
                [item.path for item in baseline.entries],
                ["build.h5", "build.yaml"],
            )
            write(build / "products" / "outer.h5", "changed product")
            self.assertEqual(baseline, DATA.verify_fingerprint(resource))
            write(build / "build.h5", "changed state")
            self.assertNotEqual(
                baseline.fingerprint,
                DATA.observe_fingerprint(resource).fingerprint,
            )

    def test_identity_files_cache_reuses_exact_declared_file_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            build = entry / "build"
            write(build / "build.h5", "state")
            write(build / "build.yaml", "mode: test\n")
            resource = DATA.build_identity_directory(
                "build",
                "build",
                ("build.h5", "build.yaml"),
                entry_root=entry,
            )
            observed = DATA.verify_fingerprint(resource)
            assert observed is not None
            cached = DATA.fingerprint_observation_record(resource, observed)

            with mock.patch.object(
                DATA,
                "observe_fingerprint",
                side_effect=AssertionError("identity files must not be rehashed"),
            ):
                reused = DATA.verify_fingerprint(resource, cached=cached)

            assert reused is not None
            self.assertTrue(reused.identity_reused)

    def test_identity_patterns_track_bounded_wildcard_membership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            build = entry / "build"
            write(build / "build.h5", "state")
            write(build / "build.yaml", "mode: test\n")
            write(build / "build.log", "completed outer 1\n")
            write(build / "maps-hpx6.h5", "map 6")
            write(build / "products" / "outer.h5", "large product")

            resource = DATA.build_identity_pattern_directory(
                "build",
                "build",
                ("maps-*.h5", "build.yaml", "build.log", "build.h5"),
                entry_root=entry,
            )
            baseline = DATA.verify_fingerprint(resource)
            assert baseline is not None

            self.assertEqual(
                resource.fingerprint.patterns,
                ("build.h5", "build.log", "build.yaml", "maps-*.h5"),
            )
            self.assertEqual(
                [item.path for item in baseline.entries],
                ["build.h5", "build.log", "build.yaml", "maps-hpx6.h5"],
            )
            write(build / "products" / "outer.h5", "changed product")
            self.assertEqual(baseline, DATA.verify_fingerprint(resource))

            write(build / "maps-hpx9.h5", "map 9")
            changed = DATA.observe_fingerprint(resource)
            self.assertNotEqual(baseline.fingerprint, changed.fingerprint)
            self.assertEqual(
                [item.path for item in changed.entries],
                [
                    "build.h5",
                    "build.log",
                    "build.yaml",
                    "maps-hpx6.h5",
                    "maps-hpx9.h5",
                ],
            )

    def test_identity_patterns_allow_an_empty_wildcard_family(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            build = entry / "build"
            write(build / "build.h5", "state")

            resource = DATA.build_identity_pattern_directory(
                "build",
                "build",
                ("build.h5", "maps-*.h5"),
                entry_root=entry,
            )
            baseline = DATA.verify_fingerprint(resource)
            assert baseline is not None
            self.assertEqual([item.path for item in baseline.entries], ["build.h5"])

            write(build / "maps-hpx6.h5", "map 6")
            changed = DATA.observe_fingerprint(resource)
            self.assertNotEqual(baseline.fingerprint, changed.fingerprint)
            self.assertEqual(
                [item.path for item in changed.entries],
                ["build.h5", "maps-hpx6.h5"],
            )

    def test_identity_patterns_reject_recursive_empty_and_overlapping_selectors(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            build = entry / "build"
            write(build / "build.h5", "state")

            for patterns in (
                ("**/outer.h5",),
                ("maps-*.h5",),
                ("build.h5", "build.*"),
            ):
                with (
                    self.subTest(patterns=patterns),
                    self.assertRaises(DATA.DataContractError),
                ):
                    DATA.build_identity_pattern_directory(
                        "build",
                        "build",
                        patterns,
                        entry_root=entry,
                    )

    def test_identity_pattern_candidate_scan_stops_at_the_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build"
            build.mkdir()
            resource = DATA.InputResource(
                "build",
                "directory",
                build.as_posix(),
                DATA.Fingerprint(
                    "identity-patterns-sha256-v1",
                    digest="0" * 64,
                    patterns=("maps-*.h5",),
                ),
                False,
                build.resolve().as_posix(),
            )
            observed = 0

            class FakeEntry:
                def __init__(self, path: Path):
                    self.path = path.as_posix()

            class FakeScan:
                def __enter__(self) -> FakeScan:
                    return self

                def __exit__(self, *args: object) -> None:
                    return None

                def __iter__(self) -> object:
                    nonlocal observed
                    for name in ("first.txt", "second.txt", "must-not-be-read"):
                        observed += 1
                        if observed > 2:
                            raise AssertionError("enumeration continued past the bound")
                        yield FakeEntry(build / name)

            with (
                mock.patch.object(DATA, "MAX_IDENTITY_PATTERN_CANDIDATES", 1),
                mock.patch.object(DATA.os, "scandir", return_value=FakeScan()),
                self.assertRaisesRegex(
                    DATA.DataContractError, "directory.membership.invalid"
                ),
            ):
                DATA.identity_pattern_paths(resource)
            self.assertEqual(observed, 2)

    def test_identity_patterns_scan_each_wildcard_parent_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build"
            write(build / "maps-hpx6.h5", "map")
            write(build / "metrics-hpx6.csv", "metric")
            resource = DATA.InputResource(
                "build",
                "directory",
                build.as_posix(),
                DATA.Fingerprint(
                    "identity-patterns-sha256-v1",
                    digest="0" * 64,
                    patterns=("maps-*.h5", "metrics-*.csv"),
                ),
                False,
                build.resolve().as_posix(),
            )

            with mock.patch.object(
                DATA.os, "scandir", wraps=DATA.os.scandir
            ) as scandir:
                paths = DATA.identity_pattern_paths(resource)

            self.assertEqual(tuple(paths), ("maps-hpx6.h5", "metrics-hpx6.csv"))
            scandir.assert_called_once_with(build.resolve())

    def test_identity_file_paths_are_strict_bounded_and_non_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            build = entry / "build"
            write(build / "build.h5", "state")
            payload = {
                "schema": DATA.DATA_SCHEMA,
                "inputs": [
                    {
                        "name": "build",
                        "kind": "directory",
                        "location": "build",
                        "fingerprint": {
                            "algorithm": "identity-files-sha256-v1",
                            "files": ["../outside", "build.h5"],
                            "digest": "0" * 64,
                        },
                        "origin": False,
                    }
                ],
            }
            write(entry / "data.json", json.dumps(payload))
            with self.assertRaisesRegex(
                DATA.DataContractError, "data.declaration.invalid"
            ):
                DATA.load_data_file(entry / "data.json", entry_root=entry)

            payload["inputs"][0]["fingerprint"]["files"] = ["missing.h5"]
            write(entry / "data.json", json.dumps(payload))
            resource = DATA.load_data_file(
                entry / "data.json", entry_root=entry
            ).inputs[0]
            with self.assertRaisesRegex(DATA.DataContractError, "data.target.missing"):
                DATA.verify_fingerprint(resource)

            (build / "missing.h5").symlink_to(build / "build.h5")
            with self.assertRaisesRegex(
                DATA.DataContractError, "data.declaration.invalid"
            ):
                DATA.verify_fingerprint(resource)

    def test_cross_entry_consistency_uses_kind_fingerprint_and_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_entry, source = data_fixture(root)
            second_entry = root / "docs" / "study" / "entries" / "2026-09-01-e002-study"
            second_entry.mkdir(parents=True)
            payload = json.loads(
                (first_entry / "data.json").read_text(encoding="utf-8")
            )
            payload["inputs"][0]["location"] = str(source)
            write(second_entry / "data.json", json.dumps(payload))
            first = DATA.load_data_file(
                first_entry / "data.json", entry_root=first_entry
            )
            second = DATA.load_data_file(
                second_entry / "data.json", entry_root=second_entry
            )
            DATA.validate_log_consistency((first, second))

            payload["inputs"][0]["origin"] = False
            write(second_entry / "data.json", json.dumps(payload))
            second = DATA.load_data_file(
                second_entry / "data.json", entry_root=second_entry
            )
            with self.assertRaisesRegex(
                DATA.DataContractError, "data.declaration.conflict"
            ):
                DATA.validate_log_consistency((first, second))

    def test_directory_hash_is_deterministic_and_tracks_every_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "entry"
            collection = entry / "data" / "collection"
            write(collection / "a.txt", "a")
            (collection / "empty").mkdir()
            resource = DATA.InputResource(
                "collection",
                "directory",
                "data/collection",
                DATA.Fingerprint("directory-sha256-v1", digest="0" * 64),
                True,
                str(collection),
            )

            baseline = DATA.observe_fingerprint(resource)
            self.assertEqual(baseline, DATA.observe_fingerprint(resource))
            self.assertEqual(
                [entry.path for entry in baseline.entries], ["a.txt", "empty"]
            )

            write(collection / "b.txt", "b")
            added = DATA.observe_fingerprint(resource)
            self.assertNotEqual(baseline.fingerprint, added.fingerprint)
            (collection / "b.txt").unlink()
            self.assertEqual(baseline, DATA.observe_fingerprint(resource))

            (collection / "a.txt").rename(collection / "renamed.txt")
            renamed = DATA.observe_fingerprint(resource)
            self.assertNotEqual(baseline.fingerprint, renamed.fingerprint)
            (collection / "renamed.txt").unlink()
            (collection / "renamed.txt").mkdir()
            type_changed = DATA.observe_fingerprint(resource)
            self.assertNotEqual(renamed.fingerprint, type_changed.fingerprint)
            (collection / "renamed.txt").rmdir()
            write(collection / "a.txt", "changed")
            changed = DATA.observe_fingerprint(resource)
            self.assertNotEqual(baseline.fingerprint, changed.fingerprint)

    def test_directory_rejects_nested_symlink_and_resource_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = root / "collection"
            write(collection / "a.txt", "a")
            (collection / "alias.txt").symlink_to(collection / "a.txt")
            resource = DATA.InputResource(
                "collection",
                "directory",
                str(collection),
                DATA.Fingerprint("directory-sha256-v1", digest="0" * 64),
                False,
                str(collection),
            )
            with self.assertRaisesRegex(
                DATA.DataContractError, "directory.membership.invalid"
            ):
                DATA.observe_fingerprint(resource)

            (collection / "alias.txt").unlink()
            with mock.patch.object(DATA, "MAX_DIRECTORY_ENTRIES", 0):
                with self.assertRaisesRegex(
                    DATA.DataContractError, "directory.membership.invalid"
                ):
                    DATA.observe_fingerprint(resource)

    def test_directory_rejects_a_change_between_hash_and_cache_observation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            collection = Path(directory) / "collection"
            source = collection / "a.txt"
            write(source, "before")
            resource = DATA.InputResource(
                "collection",
                "directory",
                str(collection),
                DATA.Fingerprint("directory-sha256-v1", digest="0" * 64),
                False,
                str(collection),
            )
            original = DATA._hash_file_observation

            def mutate_after_hash(path: Path) -> object:
                observation = original(path)
                write(path, "after")
                return observation

            with mock.patch.object(
                DATA, "_hash_file_observation", side_effect=mutate_after_hash
            ):
                with self.assertRaisesRegex(
                    DATA.DataContractError, "provenance.observation.unavailable"
                ):
                    DATA.observe_fingerprint(resource)

    def test_directory_cache_rejects_a_change_during_hot_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            source = entry / "collection" / "a.txt"
            write(source, "before")
            resource = DATA.build_local_input(
                "collection", "directory", "collection", entry_root=entry
            )
            observed = DATA.verify_fingerprint(resource)
            assert observed is not None
            cached = DATA.fingerprint_observation_record(resource, observed)
            original = DATA._directory_member_metadata
            changed = False

            def mutate_after_metadata(root: Path, paths: tuple[Path, ...]) -> object:
                nonlocal changed
                result = original(root, paths)
                if not changed:
                    write(source, "after")
                    changed = True
                return result

            with mock.patch.object(
                DATA, "_directory_member_metadata", side_effect=mutate_after_metadata
            ):
                with self.assertRaisesRegex(
                    DATA.DataContractError, "provenance.observation.unavailable"
                ):
                    DATA.verify_fingerprint(resource, cached=cached)


class RetentionFileTests(unittest.TestCase):
    def test_exact_and_directory_records_are_strict_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            write(entry / "data" / "debug.json", "{}\n")
            write(entry / "data" / "traces" / "trace.json", "{}\n")
            write(
                entry / "retention.json",
                json.dumps(
                    {
                        "schema": RETENTION.RETENTION_SCHEMA,
                        "records": [
                            {
                                "id": "traces",
                                "directory": "data/traces",
                                "membership": "all-descendants",
                            },
                            {"id": "debug", "paths": ["data/debug.json"]},
                        ],
                    }
                ),
            )

            retained = RETENTION.load_retention_file(
                entry / "retention.json", entry_root=entry
            )

            self.assertEqual(
                [record.id for record in retained.records], ["traces", "debug"]
            )
            self.assertEqual(
                [
                    record["id"]
                    for record in json.loads(retained.canonical_json())["records"]
                ],
                ["debug", "traces"],
            )
            self.assertEqual(len(retained.identity), 64)

    def test_oversized_retention_is_rejected_before_whole_file_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            write(
                entry / "retention.json",
                '{"schema":"research-log-retention/v1","records":[]}',
            )
            with (
                mock.patch.object(RETENTION, "MAX_RETENTION_FILE_BYTES", 4),
                mock.patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError("whole-file read is forbidden"),
                ),
            ):
                with self.assertRaisesRegex(
                    RETENTION.RetentionContractError,
                    "retention.declaration.invalid",
                ):
                    RETENTION.load_retention_file(
                        entry / "retention.json", entry_root=entry
                    )

    def test_overlap_missing_target_and_duplicate_key_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            write(entry / "data" / "traces" / "trace.json", "{}\n")
            payload = {
                "schema": RETENTION.RETENTION_SCHEMA,
                "records": [
                    {
                        "id": "tree",
                        "directory": "data/traces",
                        "membership": "all-descendants",
                    },
                    {"id": "file", "paths": ["data/traces/trace.json"]},
                ],
            }
            write(entry / "retention.json", json.dumps(payload))
            with self.assertRaisesRegex(
                RETENTION.RetentionContractError, "retention.declaration.invalid"
            ):
                RETENTION.load_retention_file(
                    entry / "retention.json", entry_root=entry
                )

            payload["records"] = [{"id": "missing", "paths": ["data/missing.json"]}]
            write(entry / "retention.json", json.dumps(payload))
            with self.assertRaisesRegex(
                RETENTION.RetentionContractError, "retention.target.missing"
            ):
                RETENTION.load_retention_file(
                    entry / "retention.json", entry_root=entry
                )

            write(
                entry / "retention.json",
                '{"schema":"x","schema":"research-log-retention/v1","records":[]}',
            )
            with self.assertRaisesRegex(
                RETENTION.RetentionContractError, "retention.declaration.invalid"
            ):
                RETENTION.load_retention_file(
                    entry / "retention.json", entry_root=entry
                )


class BoundedFilesystemTests(unittest.TestCase):
    def test_descendant_enumeration_stops_at_the_first_over_limit_entry(
        self,
    ) -> None:
        observed = 0

        class FakeEntry:
            def __init__(self, path: str):
                self.path = path

            def is_dir(self, *, follow_symlinks: bool) -> bool:
                if follow_symlinks:
                    raise AssertionError("symlinks must not be followed")
                return False

        class FakeScan:
            def __enter__(self) -> FakeScan:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def __iter__(self) -> object:
                nonlocal observed
                for name in ("first", "second", "must-not-be-read"):
                    observed += 1
                    if observed > 2:
                        raise AssertionError("enumeration continued past the bound")
                    yield FakeEntry(f"/root/{name}")

        with mock.patch.object(FILESYSTEM.os, "scandir", return_value=FakeScan()):
            with self.assertRaises(FILESYSTEM.BoundedTraversalError):
                FILESYSTEM.bounded_descendants(Path("/root"), maximum_entries=1)
        self.assertEqual(observed, 2)


if __name__ == "__main__":
    unittest.main()
