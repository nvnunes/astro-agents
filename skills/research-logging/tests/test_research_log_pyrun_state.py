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
    ExecutionRecipe,
    ObservedExecution,
    PyrunExecution,
    PyrunFile,
    PyrunStateError,
    confirm_execution_locked,
    execution_id,
    load_pyrun_state,
    publish_execution_locked,
    retire_execution_locked,
    update_slow_locked,
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
    confirmed: bool = True,
    slow: bool = False,
    last_run_at: str | None = "2030-01-01T00:00:00Z",
) -> PyrunExecution:
    recipe = recipe or _recipe()
    return PyrunExecution(
        confirmed,
        slow,
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
        first = _execution(recipe, confirmed=False, slow=False, last_run_at=None)
        second = PyrunExecution(
            True,
            True,
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
            cases.append((canonical.replace('"slow": false', '"slow": null'), "slow"))
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
                '{"executions":{},"schema":"research-log-pyrun/v1",'
                '"schema":"research-log-pyrun/v1"}\n',
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

    def test_policy_confirmation_and_retirement_preserve_owned_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            initial = _execution(confirmed=False, last_run_at=None)
            identity = execution_id(initial.recipe)
            publish_execution_locked(entry, initial, project_root=root)
            before = load_pyrun_state(
                entry / PYRUN_FILENAME, entry_root=entry, project_root=root
            ).executions[identity]

            changed = update_slow_locked(
                entry, (identity,), slow=True, project_root=root
            ).executions[identity]
            self.assertEqual(changed.slow, True)
            self.assertEqual(
                changed,
                PyrunExecution(
                    before.confirmed,
                    True,
                    before.last_run_at,
                    before.runner,
                    before.environment_profile,
                    before.execution_contract,
                    before.recipe,
                    before.observed,
                ),
            )

            confirmed = confirm_execution_locked(
                entry, identity, project_root=root
            ).executions[identity]
            self.assertTrue(confirmed.confirmed)
            self.assertIsNone(confirmed.last_run_at)
            self.assertTrue(confirmed.slow)

            retired = retire_execution_locked(
                entry, identity, project_root=root
            )
            self.assertEqual(retired.executions, {})
            self.assertFalse((entry / PYRUN_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
