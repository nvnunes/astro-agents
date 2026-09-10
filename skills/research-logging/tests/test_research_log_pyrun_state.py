from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research_log_data import Fingerprint
from validation.pyrun_state import (
    PYRUN_ENVIRONMENT_PROFILE,
    PYRUN_EXECUTION_CONTRACT,
    PYRUN_FILENAME,
    PYRUN_RUNNER,
    PYRUN_SCHEMA,
    ExecutionRecipe,
    ObservedExecution,
    PyrunExecution,
    PyrunFile,
    PyrunStateError,
    clear_reproduction_requirement_locked,
    execution_id,
    load_pyrun_state,
    portable_script_path,
    publish_execution_locked,
    retire_execution_locked,
    script_target_path,
    update_auto_reproduce_locked,
    validate_output_paths,
)


def _fingerprint(character: str = "a") -> Fingerprint:
    return Fingerprint("sha256", digest=character * 64)


def _recipe(
    *,
    output: str = "data/result.csv",
    inputs: tuple[str, ...] = ("catalog",),
    environment: tuple[tuple[str, str], ...] = (("MODE", "exact"),),
) -> ExecutionRecipe:
    return ExecutionRecipe(
        "scripts/build.py",
        ("--input-data", "<catalog>", "--output-data", output),
        environment,
        inputs,
        ((output, "file"),),
    )


def _execution(
    recipe: ExecutionRecipe | None = None,
    *,
    requires_reproduction: bool = False,
    auto_reproduce: bool = True,
    last_run_at: str | None = "2030-01-01T00:00:00Z",
) -> PyrunExecution:
    recipe = recipe or _recipe()
    return PyrunExecution(
        requires_reproduction,
        auto_reproduce,
        last_run_at,
        PYRUN_RUNNER,
        PYRUN_ENVIRONMENT_PROFILE,
        PYRUN_EXECUTION_CONTRACT,
        recipe,
        ObservedExecution(
            _fingerprint("b"),
            tuple((name, _fingerprint("c")) for name in recipe.inputs),
            (("scripts/helper.py", _fingerprint("d")),),
            tuple((name, _fingerprint("e")) for name, _ in recipe.outputs),
        ),
    )


def _entry(root: Path) -> Path:
    entry = root / "log/entries/2030-01-01-e001-test"
    (entry / "data").mkdir(parents=True)
    (entry / "scripts").mkdir()
    return entry


class PyrunStateContractTests(unittest.TestCase):
    def test_script_identity_accepts_canonical_entry_and_log_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)

            self.assertEqual(
                portable_script_path("scripts/build.py", entry_root=entry),
                "scripts/build.py",
            )
            self.assertEqual(
                portable_script_path("<log>/scripts/shared.py", entry_root=entry),
                "<log>/scripts/shared.py",
            )
            self.assertEqual(
                script_target_path(
                    "<log>/scripts/shared.py", entry_root=entry
                ),
                root / "log/scripts/shared.py",
            )
            self.assertEqual(
                portable_script_path(
                    "<project>/log/scripts/shared.py",
                    entry_root=entry,
                    project_root=root,
                    authored=True,
                ),
                "<log>/scripts/shared.py",
            )
            self.assertEqual(
                portable_script_path(
                    "<project>/shared/build.py",
                    entry_root=entry,
                    project_root=root,
                    authored=True,
                ),
                "<project>/shared/build.py",
            )
            self.assertEqual(
                script_target_path(
                    "<project>/shared/build.py",
                    entry_root=entry,
                    project_root=root,
                ),
                root / "shared/build.py",
            )
            self.assertEqual(
                portable_script_path(
                    "<project>/log/entries/2030-01-01-e001-test/scripts/build.py",
                    entry_root=entry,
                    project_root=root,
                    authored=True,
                ),
                "scripts/build.py",
            )
            for invalid in (
                "<project>/scripts/shared.py",
                "<log>/entries/2030-01-01-e001-test/scripts/build.py",
                "../scripts/shared.py",
            ):
                with self.subTest(invalid=invalid), self.assertRaises(
                    PyrunStateError
                ):
                    portable_script_path(invalid, entry_root=entry)

    def test_round_trip_is_canonical_and_identity_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            execution = _execution()
            identity = execution_id(execution.recipe)
            state = PyrunFile(entry / PYRUN_FILENAME, entry, {identity: execution})
            (entry / PYRUN_FILENAME).write_text(state.serialized(), encoding="utf-8")

            loaded = load_pyrun_state(
                entry / PYRUN_FILENAME, entry_root=entry, project_root=root
            )

            self.assertEqual(loaded, state)
            self.assertEqual(
                identity,
                "pyrun-exec/v1:18c711c494567ce3a5692501407d50ad6454f6f95425fa25bb1a48a6e53a901f",
            )
            self.assertTrue((entry / PYRUN_FILENAME).read_bytes().endswith(b"\n"))

    def test_identity_excludes_policy_observation_and_versions(self) -> None:
        recipe = _recipe()
        first = _execution(
            recipe,
            requires_reproduction=True,
            auto_reproduce=True,
            last_run_at=None,
        )
        second = PyrunExecution(
            False,
            False,
            "2031-02-03T04:05:06Z",
            PYRUN_RUNNER,
            PYRUN_ENVIRONMENT_PROFILE,
            PYRUN_EXECUTION_CONTRACT,
            recipe,
            ObservedExecution(
                _fingerprint("1"),
                (("catalog", _fingerprint("2")),),
                (),
                (("data/result.csv", _fingerprint("3")),),
            ),
        )

        self.assertEqual(execution_id(first.recipe), execution_id(second.recipe))
        changed = _recipe(environment=(("MODE", "alternate"),))
        self.assertNotEqual(execution_id(recipe), execution_id(changed))

    def test_retained_migration_fixture_and_legacy_rejection(self) -> None:
        fixtures = Path(__file__).parent / "fixtures"
        legacy_raw = (fixtures / "pyrun-exclusivity-migration-v2.json").read_text()
        current_raw = (
            fixtures / "pyrun-reproduction-requirement-v4.json"
        ).read_text()
        expected = json.loads(legacy_raw)
        expected["schema"] = PYRUN_SCHEMA
        for execution in expected["executions"].values():
            execution["requires_reproduction"] = not execution.pop("confirmed")
            execution["exclusive"] = True
        self.assertEqual(
            current_raw,
            json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            path = entry / PYRUN_FILENAME
            path.write_text(current_raw, encoding="utf-8")
            loaded = load_pyrun_state(path, entry_root=entry, project_root=root)
            self.assertEqual(loaded.schema, PYRUN_SCHEMA)
            self.assertTrue(next(iter(loaded.executions.values())).exclusive)

            prior = json.loads(current_raw)
            prior["schema"] = "research-log-pyrun/v3"
            for execution in prior["executions"].values():
                execution["confirmed"] = not execution.pop(
                    "requires_reproduction"
                )
            path.write_text(
                json.dumps(prior, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(PyrunStateError) as rejected:
                load_pyrun_state(path, entry_root=entry, project_root=root)
            self.assertEqual(rejected.exception.code, "pyrun.state.schema.unsupported")
            self.assertEqual(
                rejected.exception.observed,
                {"schema": "research-log-pyrun/v3", "supported": PYRUN_SCHEMA},
            )

            path.write_text(legacy_raw, encoding="utf-8")
            with self.assertRaises(PyrunStateError) as rejected:
                load_pyrun_state(path, entry_root=entry, project_root=root)
            self.assertEqual(rejected.exception.code, "pyrun.state.schema.unsupported")
            self.assertEqual(
                rejected.exception.observed,
                {"schema": "research-log-pyrun/v2", "supported": PYRUN_SCHEMA},
            )

    def test_strict_decoder_rejects_noncanonical_and_invalid_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            execution = _execution()
            identity = execution_id(execution.recipe)
            state = PyrunFile(entry / PYRUN_FILENAME, entry, {identity: execution})
            path = entry / PYRUN_FILENAME
            canonical = state.serialized()
            cases: list[tuple[str, str]] = []
            cases.append((canonical.rstrip("\n"), "noncanonical"))
            cases.append(
                (
                    canonical.replace(
                        '"auto_reproduce": true', '"auto_reproduce": null'
                    ),
                    "auto_reproduce",
                )
            )
            cases.append(
                (
                    canonical.replace(
                        '"last_run_at": "2030-01-01T00:00:00Z"',
                        '"last_run_at": "2030-01-01T00:00:00+00:00"',
                    ),
                    "timestamp",
                )
            )
            cases.append(
                (canonical.replace(identity, "pyrun-exec/v1:" + "0" * 64), "id")
            )
            cases.append(
                (
                    canonical.replace(
                        '"runner":', '"unknown": null,\n      "runner":', 1
                    ),
                    "field",
                )
            )
            for raw, label in cases:
                with self.subTest(label=label):
                    path.write_text(raw, encoding="utf-8")
                    with self.assertRaises(PyrunStateError):
                        load_pyrun_state(path, entry_root=entry, project_root=root)

            path.write_text(
                '{"executions":{},"schema":"research-log-pyrun/v4",'
                '"schema":"research-log-pyrun/v4"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PyrunStateError, "duplicate JSON key"):
                load_pyrun_state(path, entry_root=entry, project_root=root)

    def test_decoder_rejects_observation_key_and_output_ownership_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            first = _execution(_recipe(output="data/bundle"))
            second_recipe = _recipe(output="data/bundle/member.csv")
            second = _execution(second_recipe)
            first_id = execution_id(first.recipe)
            second_id = execution_id(second.recipe)
            state = PyrunFile(
                entry / PYRUN_FILENAME,
                entry,
                {first_id: first, second_id: second},
            )
            path = entry / PYRUN_FILENAME
            path.write_text(state.serialized(), encoding="utf-8")

            with self.assertRaisesRegex(PyrunStateError, "output_ownership_overlap"):
                load_pyrun_state(path, entry_root=entry, project_root=root)

            value = json.loads(PyrunFile(path, entry, {first_id: first}).serialized())
            value["executions"][first_id]["observed"]["outputs"] = {}
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PyrunStateError, "observed_output_keys"):
                load_pyrun_state(path, entry_root=entry, project_root=root)

    def test_output_set_rejects_duplicates_and_ancestor_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            for outputs in (
                ("data/result.csv", "data/result.csv"),
                ("data/bundle", "data/bundle/member.csv"),
            ):
                with (
                    self.subTest(outputs=outputs),
                    self.assertRaises(PyrunStateError),
                ):
                    validate_output_paths(
                        outputs, entry_root=entry, project_root=root
                    )

    def test_publication_rejects_in_memory_key_coercion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            for recipe in (
                _recipe(environment=(("MODE", "first"), ("MODE", "second"))),
                ExecutionRecipe(
                    "scripts/build.py",
                    (),
                    (),
                    (),
                    (("data/result.csv", "file"), ("data/result.csv", "file")),
                ),
            ):
                with self.subTest(recipe=recipe), self.assertRaises(PyrunStateError):
                    publish_execution_locked(
                        entry, _execution(recipe), project_root=root
                    )
                self.assertFalse((entry / PYRUN_FILENAME).exists())


class PyrunStateLifecycleTests(unittest.TestCase):
    def test_publication_replaces_every_overlapping_execution_in_full(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            first_recipe = ExecutionRecipe(
                "scripts/build.py",
                (),
                (),
                (),
                (("data/first.csv", "file"), ("data/shared.csv", "file")),
            )
            first = _execution(first_recipe)
            publish_execution_locked(entry, first, project_root=root)
            second_recipe = ExecutionRecipe(
                "scripts/build.py",
                ("--mode", "new"),
                (),
                (),
                (("data/shared.csv", "file"), ("data/third.csv", "file")),
            )
            second = _execution(second_recipe)

            result = publish_execution_locked(entry, second, project_root=root)

            self.assertEqual(set(result.executions), {execution_id(second_recipe)})
            self.assertNotIn("data/first.csv", result.serialized())

    def test_policy_requirement_and_retirement_preserve_owned_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            initial = _execution(requires_reproduction=True, last_run_at=None)
            identity = execution_id(initial.recipe)
            publish_execution_locked(entry, initial, project_root=root)
            before = load_pyrun_state(
                entry / PYRUN_FILENAME, entry_root=entry, project_root=root
            ).executions[identity]

            changed = update_auto_reproduce_locked(
                entry, (identity,), auto_reproduce=False, project_root=root
            ).executions[identity]
            self.assertFalse(changed.auto_reproduce)
            self.assertEqual(
                changed,
                PyrunExecution(
                    before.requires_reproduction,
                    False,
                    before.last_run_at,
                    before.runner,
                    before.environment_profile,
                    before.execution_contract,
                    before.recipe,
                    before.observed,
                ),
            )

            updated = clear_reproduction_requirement_locked(
                entry, identity, project_root=root
            ).executions[identity]
            self.assertFalse(updated.requires_reproduction)
            self.assertIsNone(updated.last_run_at)
            self.assertFalse(updated.auto_reproduce)

            retired = retire_execution_locked(
                entry, identity, project_root=root
            )
            self.assertEqual(retired.executions, {})
            self.assertFalse((entry / PYRUN_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
