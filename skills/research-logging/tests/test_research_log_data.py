from __future__ import annotations

import json
import os
import subprocess
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


def data_fixture(root: Path) -> tuple[Path, Path]:
    entry = root / "docs" / "study" / "entries" / "2026-09-01-e001-study"
    source = entry / "data" / "source.csv"
    write(source, "value\n1\n")
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
                        "identity": {"algorithm": "sha256"},
                        "origin": True,
                    }
                ],
            },
            indent=2,
        )
        + "\n",
    )
    return entry, source


def git_repository(root: Path) -> tuple[Path, str, str]:
    repository = root / "source-repository"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    source = repository / "source.txt"
    source.write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "source.txt"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Research Log Tests",
            "-c",
            "user.email=research-log@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    blob = subprocess.check_output(
        ["git", "rev-parse", "HEAD:source.txt"], cwd=repository, text=True
    ).strip()
    return repository, commit, blob


class DataFileTests(unittest.TestCase):
    def test_generated_file_accepts_explicit_evidence_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry, _ = data_fixture(Path(directory))
            path = entry / "data.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            item = payload["inputs"][0]
            item["origin"] = False
            item["reproduction_comparison"] = {
                "contract": DATA.EVIDENCE_COMPARISON_CONTRACT,
                "profile": "evidence",
            }
            path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = DATA.load_data_file(path, entry_root=entry)

            self.assertEqual(loaded.inputs[0].comparison.profile, "evidence")
            self.assertEqual(
                json.loads(loaded.canonical_json())["inputs"][0]["reproduction_comparison"],
                item["reproduction_comparison"],
            )

    def test_evidence_comparison_rejects_origins_directories_and_unknown_forms(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry, _ = data_fixture(Path(directory))
            path = entry / "data.json"
            base = json.loads(path.read_text(encoding="utf-8"))
            comparison = {
                "contract": DATA.EVIDENCE_COMPARISON_CONTRACT,
                "profile": "evidence",
            }
            replacements = (
                {"reproduction_comparison": None, "origin": False},
                {"reproduction_comparison": comparison},
                {
                    "reproduction_comparison": comparison,
                    "kind": "directory",
                    "location": "data",
                    "identity": {"algorithm": "directory-sha256-v1"},
                    "origin": False,
                },
                {
                    "reproduction_comparison": {
                        "contract": DATA.EVIDENCE_COMPARISON_CONTRACT,
                        "profile": "approximate",
                    },
                    "origin": False,
                },
            )
            for replacement in replacements:
                with self.subTest(replacement=replacement):
                    payload = json.loads(json.dumps(base))
                    payload["inputs"][0].update(replacement)
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(
                        DATA.DataContractError, "data.declaration.invalid"
                    ):
                        DATA.load_data_file(path, entry_root=entry)

    def test_input_token_parts_enforces_complete_member_syntax(self) -> None:
        self.assertEqual(
            DATA.input_token_parts("<results>"), ("results", None, None)
        )
        self.assertEqual(
            DATA.input_token_parts("<results:commit>"),
            ("results", "commit", None),
        )
        self.assertEqual(
            DATA.input_token_parts("<results>/nested/file.csv"),
            ("results", None, "nested/file.csv"),
        )
        for token in (
            "<results>/../secret.csv",
            "<results>/nested//file.csv",
            "<results>/nested\\file.csv",
            "<results>/https://host/file.csv",
            "<results:commit>/nested/file.csv",
            "<results:branch>",
        ):
            with self.subTest(token=token):
                self.assertIsNone(DATA.input_token_parts(token))

    def test_git_repository_identity_is_the_exact_commit_not_live_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "entry"
            entry.mkdir()
            repository, commit, blob = git_repository(root)

            resource = DATA.build_git_repository_input(
                "source-repository",
                repository.as_posix(),
                commit,
                entry_root=entry,
            )
            self.assertEqual(resource.kind, "git-repository")
            self.assertTrue(resource.origin)
            self.assertEqual(resource.identity.commit, commit)
            self.assertEqual(
                resource.material_identity,
                f"{DATA.GIT_COMMIT_ALGORITHM}:{commit}",
            )

            bare_repository = root / "source-repository.git"
            subprocess.run(
                ["git", "clone", "--bare", str(repository), str(bare_repository)],
                check=True,
                capture_output=True,
            )
            bare_resource = DATA.build_git_repository_input(
                "bare-source-repository",
                bare_repository.as_posix(),
                commit,
                entry_root=entry,
            )
            self.assertEqual(
                bare_resource.material_identity, resource.material_identity
            )

            worktree = root / "source-worktree"
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(worktree), commit],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            worktree_resource = DATA.build_git_repository_input(
                "source-worktree",
                worktree.as_posix(),
                commit,
                entry_root=entry,
            )
            self.assertEqual(
                worktree_resource.material_identity, resource.material_identity
            )

            (repository / "source.txt").write_text("dirty\n", encoding="utf-8")
            (repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")
            DATA.observe_fingerprint(resource)

            moved = root / "moved-repository"
            repository.rename(moved)
            moved_resource = DATA.build_git_repository_input(
                "moved-source-repository",
                moved.as_posix(),
                commit,
                entry_root=entry,
            )
            self.assertEqual(
                moved_resource.material_identity, resource.material_identity
            )

            for invalid in (blob, "0" * 40):
                with (
                    self.subTest(commit=invalid),
                    self.assertRaisesRegex(
                        DATA.DataContractError, "data.fingerprint.mismatch"
                    ),
                ):
                    invalid_resource = DATA.build_git_repository_input(
                        "invalid-repository",
                        moved.as_posix(),
                        invalid,
                        entry_root=entry,
                    )
                    DATA.require_matching_observation(
                        DATA.Fingerprint(DATA.GIT_COMMIT_ALGORITHM, digest=invalid),
                        DATA.observe_fingerprint(invalid_resource),
                    )

    def test_git_repository_rejects_invalid_forms_and_non_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = root / "entry"
            entry.mkdir()
            repository, commit, _ = git_repository(root)
            path = entry / "data.json"
            base = {
                "name": "source-repository",
                "kind": "git-repository",
                "location": repository.as_posix(),
                "identity": {
                    "algorithm": DATA.GIT_COMMIT_ALGORITHM,
                    "commit": commit,
                },
                "origin": True,
            }
            for replacement in (
                {"origin": False},
                {
                    "identity": {
                        "algorithm": DATA.GIT_COMMIT_ALGORITHM,
                        "commit": commit[:12],
                    }
                },
            ):
                payload = {**base, **replacement}
                path.write_text(
                    json.dumps({"schema": DATA.DATA_SCHEMA, "inputs": [payload]}),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    DATA.DataContractError, "data.declaration.invalid"
                ):
                    DATA.load_data_file(path, entry_root=entry)

            not_repository = root / "not-repository"
            not_repository.mkdir()
            resource = DATA.build_git_repository_input(
                    "missing-repository",
                    not_repository.as_posix(),
                    commit,
                    entry_root=entry,
                )
            with self.assertRaisesRegex(DATA.DataContractError, "data.target.missing"):
                DATA.observe_fingerprint(resource)

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
            self.assertEqual(
                DATA.observe_fingerprint(data_file.inputs[0]).fingerprint.algorithm,
                "sha256",
            )

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
                "identity": {"algorithm": "immutable-source", "value": "v2"},
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
                "identity": {"algorithm": "sha256"},
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
                            "identity": {"algorithm": "directory-sha256-v1"},
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

    def test_other_maintained_entry_material_links_are_valid_and_retargeted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry, _ = data_fixture(root)
            other_log = root / "docs" / "other"
            source_entry = other_log / "entries" / "2026-09-02-e002-source"
            write(other_log.with_suffix(".md"), "# Other\n")
            first = root / "output" / "first"
            second = root / "output" / "second"
            write(first / "value.txt", "one\n")
            write(second / "value.txt", "two\n")
            source_entry.mkdir(parents=True)

            for material_root in ("data", "images"):
                with self.subTest(material_root=material_root):
                    link = source_entry / material_root
                    link.symlink_to(first, target_is_directory=True)
                    target = link / "value.txt"
                    relative = Path(os.path.relpath(target, entry)).as_posix()
                    resource = DATA.build_local_input(
                        "other-value", "file", relative, entry_root=entry
                    )
                    self.assertEqual(
                        resource.canonical_target, str((first / "value.txt").resolve())
                    )
                    self.assertEqual(
                        DATA.normalize_input_location(str(target), entry_root=entry),
                        str(target),
                    )
                    before = DATA.observe_fingerprint(resource).fingerprint.digest

                    link.unlink()
                    link.symlink_to(second, target_is_directory=True)
                    updated = DATA.build_local_input(
                        "other-value", "file", relative, entry_root=entry
                    )
                    after = DATA.observe_fingerprint(updated).fingerprint.digest
                    self.assertEqual(
                        updated.canonical_target, str((second / "value.txt").resolve())
                    )
                    self.assertNotEqual(before, after)
                    link.unlink()

    def test_material_link_exception_requires_a_maintained_entry_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry, _ = data_fixture(root)
            retained = root / "output" / "retained"
            write(retained / "value.txt", "value\n")
            fake = root / "docs" / "unmaintained" / "entries" / "2026-09-02-e002-source"
            fake.mkdir(parents=True)
            (fake / "data").symlink_to(retained, target_is_directory=True)
            nested = entry / "data" / "alias"
            nested.symlink_to(retained, target_is_directory=True)

            for target in (fake / "data/value.txt", nested / "value.txt"):
                with self.subTest(target=target):
                    location = Path(os.path.relpath(target, entry)).as_posix()
                    with self.assertRaisesRegex(
                        DATA.DataContractError, "data.declaration.invalid"
                    ):
                        DATA.build_local_input(
                            "invalid-alias", "file", location, entry_root=entry
                        )

    def test_data_registry_remains_declarative_when_file_bytes_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry, source = data_fixture(Path(directory))
            resource = DATA.load_data_file(
                entry / "data.json", entry_root=entry
            ).inputs[0]
            source.write_text("value\n2\n", encoding="utf-8")

            observation = DATA.observe_fingerprint(resource)

            self.assertEqual(observation.fingerprint.algorithm, "sha256")

    def test_reference_resolves_stable_entry_with_only_split_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entries = Path(directory) / "study" / "entries"
            producer = entries / "2026-09-01-e002-producer"
            consumer = entries / "2026-09-02-e003-consumer"
            write(producer / "e002a.md", "# Part A\n")
            write(producer / "e002i.md", "# Part I\n")
            write(
                producer / "data.json",
                json.dumps(
                    {
                        "schema": DATA.DATA_SCHEMA,
                        "inputs": [
                            {
                                "name": "result",
                                "kind": "file",
                                "location": "data/result.csv",
                                "identity": {"algorithm": "sha256"},
                                "origin": False,
                            }
                        ],
                    }
                ),
            )
            write(
                consumer / "data.json",
                json.dumps(
                    {
                        "schema": DATA.DATA_SCHEMA,
                        "inputs": [{"from_entry": "e002", "name": "result"}],
                    }
                ),
            )

            loaded = DATA.load_data_file(
                consumer / "data.json", entry_root=consumer
            )

            self.assertEqual(loaded.inputs[0].reference_entry, "e002")
            self.assertEqual(
                loaded.inputs[0].canonical_target,
                str((producer / "data/result.csv").resolve()),
            )

            duplicate = entries / "2026-09-03-e002-duplicate"
            write(duplicate / "data.json", (producer / "data.json").read_text())
            with self.assertRaisesRegex(DATA.DataContractError, "matches.*2"):
                DATA.load_data_file(consumer / "data.json", entry_root=consumer)

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
            baseline = DATA.observe_fingerprint(resource)

            self.assertEqual(resource.identity.files, ("build.h5", "build.yaml"))
            self.assertEqual(
                resource.identity.as_dict()["algorithm"],
                "identity-files-sha256-v1",
            )
            self.assertEqual(
                [item.path for item in baseline.entries],
                ["build.h5", "build.yaml"],
            )
            write(build / "products" / "outer.h5", "changed product")
            self.assertEqual(baseline, DATA.observe_fingerprint(resource))
            write(build / "build.h5", "changed state")
            self.assertNotEqual(
                baseline.fingerprint,
                DATA.observe_fingerprint(resource).fingerprint,
            )

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
            baseline = DATA.observe_fingerprint(resource)

            self.assertEqual(
                resource.identity.patterns,
                ("build.h5", "build.log", "build.yaml", "maps-*.h5"),
            )
            self.assertEqual(
                [item.path for item in baseline.entries],
                ["build.h5", "build.log", "build.yaml", "maps-hpx6.h5"],
            )
            write(build / "products" / "outer.h5", "changed product")
            self.assertEqual(baseline, DATA.observe_fingerprint(resource))

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
            baseline = DATA.observe_fingerprint(resource)
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
                    resource = DATA.build_identity_pattern_directory(
                        "build",
                        "build",
                        patterns,
                        entry_root=entry,
                    )
                    DATA.observe_fingerprint(resource)

    def test_identity_pattern_candidate_scan_stops_at_the_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build"
            build.mkdir()
            resource = DATA.InputResource(
                "build",
                "directory",
                build.as_posix(),
                DATA.ResourceIdentity(
                    "identity-patterns-sha256-v1",
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
                DATA.ResourceIdentity(
                    "identity-patterns-sha256-v1",
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
                        "identity": {
                            "algorithm": "identity-files-sha256-v1",
                            "files": ["../outside", "build.h5"],
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

            payload["inputs"][0]["identity"]["files"] = ["missing.h5"]
            write(entry / "data.json", json.dumps(payload))
            resource = DATA.load_data_file(
                entry / "data.json", entry_root=entry
            ).inputs[0]
            with self.assertRaisesRegex(DATA.DataContractError, "data.target.missing"):
                DATA.observe_fingerprint(resource)

            (build / "missing.h5").symlink_to(build / "build.h5")
            with self.assertRaisesRegex(
                DATA.DataContractError, "data.declaration.invalid"
            ):
                DATA.observe_fingerprint(resource)

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
                DATA.ResourceIdentity("directory-sha256-v1"),
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
                DATA.ResourceIdentity("directory-sha256-v1"),
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
                DATA.ResourceIdentity("directory-sha256-v1"),
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
    def test_descendant_enumeration_prunes_an_excluded_top_level_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "adhoc/one.txt", "one\n")
            write(root / "adhoc/nested/two.txt", "two\n")
            expected = root / "kept.txt"
            write(expected, "kept\n")

            observed = FILESYSTEM.bounded_descendants(
                root,
                maximum_entries=1,
                excluded_top_level_directories=frozenset({"adhoc"}),
            )

            self.assertEqual(observed, (expected,))

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
