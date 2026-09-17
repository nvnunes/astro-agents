from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from research_log_cli_test_support import fixture_parameter_roles
from research_log_data import Fingerprint
from validation import pyrun_state as pyrun_state_module
from validation.pyrun_state import (
    PYRUN_ENVIRONMENT_PROFILE,
    PYRUN_EXECUTION_CONTRACT,
    PYRUN_FILENAME,
    PYRUN_RUNNER,
    PYRUN_SCHEMA,
    ExecutionRecipe,
    ObservedExecution,
    PyrunCommand,
    PyrunExecution,
    PyrunFile,
    PyrunStateError,
    changed_execution,
    clear_reproduction_requirement_locked,
    empty_pyrun_state,
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


def _effective_fingerprint(character: str = "a") -> Fingerprint:
    return Fingerprint("python-effective-code-sha256-v1", digest=character * 64)


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
        parameter_roles=fixture_parameter_roles(
            ("--input-data", "<catalog>", "--output-data", output),
            inputs,
            ((output, "file"),),
        ),
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
            _effective_fingerprint("d"),
            tuple((name, _fingerprint("e")) for name, _ in recipe.outputs),
        ),
    )


def _entry(root: Path) -> Path:
    entry = root / "log/entries/2030-01-01-e001-test"
    (entry / "data").mkdir(parents=True)
    (entry / "scripts").mkdir()
    return entry


def _state(entry: Path, *executions: PyrunExecution) -> PyrunFile:
    return PyrunFile(
        entry / PYRUN_FILENAME,
        entry,
        {
            "build": PyrunCommand(
                {execution_id(item.recipe): item for item in executions}
            )
        },
    )


class PyrunStateContractTests(unittest.TestCase):
    def test_pythonpath_recipe_change_discards_effective_code(self) -> None:
        stored = _execution()
        for old_environment, new_environment in (
            ((('PYTHONPATH', 'src'),), (('MODE', 'exact'),)),
            ((('MODE', 'exact'),), (('PYTHONPATH', 'src'),)),
        ):
            with self.subTest(
                old_environment=old_environment,
                new_environment=new_environment,
            ):
                old_recipe = replace(stored.recipe, environment=old_environment)
                old = replace(stored, recipe=old_recipe)
                current_recipe = replace(stored.recipe, environment=new_environment)
                current = pyrun_state_module.CurrentExecution(
                    "pyrun-exec/v2:" + "f" * 64,
                    mock.Mock(auto_reproduce=True, exclusive=False),
                    current_recipe,
                )
                changed = changed_execution(
                    pyrun_state_module.ExecutionChange(current, old)
                )

                self.assertIsNone(changed.observed.effective_code)

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
                script_target_path("<log>/scripts/shared.py", entry_root=entry),
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
                with self.subTest(invalid=invalid), self.assertRaises(PyrunStateError):
                    portable_script_path(invalid, entry_root=entry)

    def test_round_trip_is_canonical_and_identity_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            execution = _execution()
            identity = execution_id(execution.recipe)
            state = _state(entry, execution)
            (entry / PYRUN_FILENAME).write_text(state.serialized(), encoding="utf-8")

            loaded = load_pyrun_state(
                entry / PYRUN_FILENAME, entry_root=entry, project_root=root
            )

            self.assertEqual(loaded, state)
            self.assertEqual(
                identity,
                "pyrun-exec/v2:1d479c1f3a502958b2e4e2cf7014b16cb5feea9f2ae6e91657774da83b645771",
            )
            self.assertTrue((entry / PYRUN_FILENAME).read_bytes().endswith(b"\n"))

    def test_roles_are_required_complete_but_excluded_from_identity(self) -> None:
        recipe = _recipe()
        ordinary_output = replace(
            recipe,
            parameter_roles=(("input-data", "input"), ("output-data", "ordinary")),
        )
        self.assertEqual(execution_id(recipe), execution_id(ordinary_output))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            path = entry / PYRUN_FILENAME
            record = _execution(recipe).as_dict()
            for roles in (
                None,
                {},
                {"input-data": "ordinary", "output-data": "output"},
            ):
                with self.subTest(roles=roles):
                    value = json.loads(json.dumps(record))
                    if roles is None:
                        del value["recipe"]["parameter_roles"]
                    else:
                        value["recipe"]["parameter_roles"] = roles
                    path.write_text(
                        json.dumps(
                            {
                                "schema": PYRUN_SCHEMA,
                                "commands": {
                                    "build": {
                                        "executions": {execution_id(recipe): value}
                                    }
                                },
                            },
                            sort_keys=True,
                            indent=2,
                        )
                        + "\n"
                    )
                    with self.assertRaises(PyrunStateError):
                        load_pyrun_state(path, entry_root=entry, project_root=root)

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
                _effective_fingerprint("4"),
                (("data/result.csv", _fingerprint("3")),),
            ),
        )

        self.assertEqual(execution_id(first.recipe), execution_id(second.recipe))
        changed = _recipe(environment=(("MODE", "alternate"),))
        self.assertEqual(execution_id(recipe), execution_id(changed))

    def test_parameter_identity_has_independent_fixed_vectors(self) -> None:
        vectors = {
            (): "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            ("",): "055539df4a0b804c58caf46c0cd2941af10d64c1395ddd8e50b5f55d945841e6",
            (
                "J",
                "H",
            ): "d436f945d7046214dd7283481408b4626370b27ff636c0a5857bb10be301d307",
            (
                "H",
                "J",
            ): "2ae82b118e2bebe6959f0843205b5ded413150def5c86a04ba918b44eb8f2a54",
            (
                "--mode",
                "",
                "--mode",
                "exact",
            ): "d4cd22e2de10cb509abcb3d0d586e5c5f8ad130e65620a112a8312fe408358db",
            (
                "--output-alias",
                "result",
            ): "d1e694807fba501e85c39f265198b5ddd4cc90f0fa4325c001d553ced60aabcf",
        }
        for parameters, digest in vectors.items():
            with self.subTest(parameters=parameters):
                self.assertEqual(execution_id(parameters), f"pyrun-exec/v2:{digest}")

    def test_identity_excludes_full_recipe_declarations_and_runner_prefix(self) -> None:
        base = _recipe()
        changed = ExecutionRecipe(
            "scripts/alternate.py",
            ("--capture-stdout", "data/run.log", "--", *base.parameters),
            (("MODE", "alternate"),),
            ("alternate-input",),
            (("data/alternate.csv", "file"),),
            (("input-data", "ordinary"), ("output-data", "ordinary")),
        )

        self.assertEqual(execution_id(base), execution_id(changed))

    def test_equal_parameter_ids_are_scoped_by_cid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            recipe = ExecutionRecipe(
                "scripts/build.py",
                ("--mode", "exact"),
                (),
                (),
                (),
                (("mode", "ordinary"),),
            )
            execution = _execution(recipe)
            identity = execution_id(execution.recipe)
            state = PyrunFile(
                entry / PYRUN_FILENAME,
                entry,
                {
                    "first": PyrunCommand({identity: execution}),
                    "second": PyrunCommand(
                        {identity: replace(execution, exclusive=True)}
                    ),
                },
            )
            (entry / PYRUN_FILENAME).write_text(state.serialized(), encoding="utf-8")

            loaded = load_pyrun_state(
                entry / PYRUN_FILENAME, entry_root=entry, project_root=root
            )

            first = loaded.execution("first", identity)
            second = loaded.execution("second", identity)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            assert first is not None and second is not None
            self.assertFalse(first.exclusive)
            self.assertTrue(second.exclusive)

    def test_partial_observation_round_trip_preserves_unavailable_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            pending = replace(
                _execution(),
                requires_reproduction=True,
                last_run_at=None,
                observed=ObservedExecution(None, (), None, ()),
            )
            state = _state(entry, pending)
            (entry / PYRUN_FILENAME).write_text(state.serialized(), encoding="utf-8")

            loaded = load_pyrun_state(
                entry / PYRUN_FILENAME, entry_root=entry, project_root=root
            )

            self.assertEqual(loaded, state)

    def test_effective_code_is_exact_nullable_v7_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            execution = _execution()
            identity = execution_id(execution.recipe)
            path = entry / PYRUN_FILENAME
            canonical = json.loads(_state(entry, execution).serialized())
            observed = canonical["commands"]["build"]["executions"][identity][
                "observed"
            ]
            for value in (
                {"algorithm": "sha256", "digest": "d" * 64},
                {
                    "algorithm": "python-effective-code-sha256-v1",
                    "digest": "D" * 64,
                },
                {"algorithm": "python-effective-code-sha256-v1"},
                {},
                [],
            ):
                with self.subTest(value=value):
                    candidate = json.loads(json.dumps(canonical))
                    candidate["commands"]["build"]["executions"][identity][
                        "observed"
                    ]["effective_code"] = value
                    path.write_text(
                        json.dumps(candidate, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaises(PyrunStateError):
                        load_pyrun_state(path, entry_root=entry, project_root=root)

            observed["effective_code"] = None
            path.write_text(
                json.dumps(canonical, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.assertIsNone(
                load_pyrun_state(
                    path, entry_root=entry, project_root=root
                ).commands["build"].executions[identity].observed.effective_code
            )

            canonical["schema"] = "research-log-pyrun/v6"
            path.write_text(
                json.dumps(canonical, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                PyrunStateError, "pyrun.state.schema.unsupported"
            ):
                load_pyrun_state(path, entry_root=entry, project_root=root)

    def test_retained_migration_fixture_and_legacy_rejection(self) -> None:
        fixtures = Path(__file__).parent / "fixtures"
        legacy_raw = (fixtures / "pyrun-exclusivity-migration-v2.json").read_text()
        current_raw = (fixtures / "pyrun-reproduction-requirement-v4.json").read_text()
        expected = json.loads(legacy_raw)
        expected["schema"] = "research-log-pyrun/v4"
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
            with self.assertRaises(PyrunStateError):
                load_pyrun_state(path, entry_root=entry, project_root=root)

            prior = json.loads(current_raw)
            prior["schema"] = "research-log-pyrun/v3"
            for execution in prior["executions"].values():
                execution["confirmed"] = not execution.pop("requires_reproduction")
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
            state = _state(entry, execution)
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
                (canonical.replace(identity, "pyrun-exec/v2:" + "0" * 64), "id")
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
                '{"commands":{},"schema":"research-log-pyrun/v7",'
                '"schema":"research-log-pyrun/v7"}\n',
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
            state = _state(entry, first, second)
            path = entry / PYRUN_FILENAME
            path.write_text(state.serialized(), encoding="utf-8")

            with self.assertRaisesRegex(PyrunStateError, "output_ownership_overlap"):
                load_pyrun_state(path, entry_root=entry, project_root=root)

            value = json.loads(_state(entry, first).serialized())
            value["commands"]["build"]["executions"][first_id]["observed"][
                "outputs"
            ] = {}
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
                    validate_output_paths(outputs, entry_root=entry, project_root=root)

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
                    parameter_roles=fixture_parameter_roles(
                        (),
                        (),
                        (("data/result.csv", "file"), ("data/result.csv", "file")),
                    ),
                ),
            ):
                with self.subTest(recipe=recipe), self.assertRaises(PyrunStateError):
                    publish_execution_locked(
                        empty_pyrun_state(entry),
                        "build",
                        _execution(recipe),
                        project_root=root,
                    )
                self.assertFalse((entry / PYRUN_FILENAME).exists())


class PyrunStateLifecycleTests(unittest.TestCase):
    def test_publication_rejects_overlap_owned_by_another_parameter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            first_recipe = ExecutionRecipe(
                "scripts/build.py",
                (),
                (),
                (),
                (("data/first.csv", "file"), ("data/shared.csv", "file")),
                parameter_roles=fixture_parameter_roles(
                    (), (), (("data/first.csv", "file"), ("data/shared.csv", "file"))
                ),
            )
            first = _execution(first_recipe)
            publish_execution_locked(
                empty_pyrun_state(entry), "build", first, project_root=root
            )
            second_recipe = ExecutionRecipe(
                "scripts/build.py",
                ("--mode", "new"),
                (),
                (),
                (("data/shared.csv", "file"), ("data/third.csv", "file")),
                parameter_roles=fixture_parameter_roles(
                    ("--mode", "new"),
                    (),
                    (("data/shared.csv", "file"), ("data/third.csv", "file")),
                ),
            )
            second = _execution(second_recipe)

            before = (entry / PYRUN_FILENAME).read_bytes()
            with self.assertRaisesRegex(PyrunStateError, "output_ownership_overlap"):
                publish_execution_locked(
                    load_pyrun_state(
                        entry / PYRUN_FILENAME,
                        entry_root=entry,
                        project_root=root,
                    ),
                    "build",
                    second,
                    project_root=root,
                )
            self.assertEqual((entry / PYRUN_FILENAME).read_bytes(), before)

    def test_publication_replaces_one_identical_complete_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            initial = _execution(last_run_at="2030-01-01T00:00:00Z")
            current = publish_execution_locked(
                empty_pyrun_state(entry), "build", initial, project_root=root
            )
            replacement = replace(
                initial,
                last_run_at="2030-01-02T00:00:00Z",
                observed=replace(
                    initial.observed,
                    outputs=(("data/result.csv", _fingerprint("f")),),
                ),
            )

            result = publish_execution_locked(
                current, "build", replacement, project_root=root
            )

            identity = execution_id(initial.recipe)
            self.assertEqual(set(result.commands["build"].executions), {identity})
            self.assertEqual(
                result.commands["build"].executions[identity], replacement
            )

    def test_publication_decodes_only_initial_and_new_executions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            first = _execution(_recipe(output="data/first.csv"))
            second = _execution(_recipe(output="data/second.csv"))
            current = publish_execution_locked(
                empty_pyrun_state(entry), "first", first, project_root=root
            )
            publish_execution_locked(current, "second", second, project_root=root)
            third = _execution(_recipe(output="data/third.csv"))

            with mock.patch.object(
                pyrun_state_module,
                "_decode_execution",
                wraps=pyrun_state_module._decode_execution,
            ) as decode:
                loaded = load_pyrun_state(
                    entry / PYRUN_FILENAME,
                    entry_root=entry,
                    project_root=root,
                )
                initial_decodes = decode.call_count
                publish_execution_locked(
                    loaded, "third", third, project_root=root
                )

            self.assertEqual(initial_decodes, 2)
            self.assertEqual(decode.call_count, 3)

    def test_publication_failure_preserves_prior_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            current = publish_execution_locked(
                empty_pyrun_state(entry), "first", _execution(), project_root=root
            )
            before = (entry / PYRUN_FILENAME).read_bytes()
            second = _execution(_recipe(output="data/second.csv"))

            with (
                mock.patch.object(
                    pyrun_state_module,
                    "_atomic_write",
                    side_effect=OSError("publication unavailable"),
                ),
                self.assertRaisesRegex(PyrunStateError, "publication unavailable"),
            ):
                publish_execution_locked(
                    current, "second", second, project_root=root
                )

            self.assertEqual((entry / PYRUN_FILENAME).read_bytes(), before)

    def test_publication_rejects_execution_limit_without_changing_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            executions = tuple(
                _execution(_recipe(output=f"data/result-{index}.csv"))
                for index in range(pyrun_state_module.MAX_EXECUTIONS)
            )
            path = entry / PYRUN_FILENAME
            path.write_text(_state(entry, *executions).serialized(), encoding="utf-8")
            current = load_pyrun_state(path, entry_root=entry, project_root=root)
            before = path.read_bytes()
            overflow = _execution(_recipe(output="data/result-overflow.csv"))

            with self.assertRaisesRegex(PyrunStateError, "'executions': 257"):
                publish_execution_locked(
                    current, "build", overflow, project_root=root
                )

            self.assertEqual(path.read_bytes(), before)

    def test_direct_edit_after_load_is_an_unsupported_overwritten_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            first = _execution(_recipe(output="data/first.csv"))
            retained = publish_execution_locked(
                empty_pyrun_state(entry), "first", first, project_root=root
            )
            outside = _execution(_recipe(output="data/outside.csv"))
            (entry / PYRUN_FILENAME).write_text(
                _state(entry, outside).serialized(), encoding="utf-8"
            )
            second = _execution(_recipe(output="data/second.csv"))

            result = publish_execution_locked(
                retained, "second", second, project_root=root
            )

            self.assertEqual(set(result.commands), {"first", "second"})
            self.assertNotIn("data/outside.csv", result.serialized())

    def test_policy_requirement_and_retirement_preserve_owned_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            initial = _execution(requires_reproduction=True, last_run_at=None)
            identity = execution_id(initial.recipe)
            publish_execution_locked(
                empty_pyrun_state(entry), "build", initial, project_root=root
            )
            before = (
                load_pyrun_state(
                    entry / PYRUN_FILENAME, entry_root=entry, project_root=root
                )
                .commands["build"]
                .executions[identity]
            )

            changed = (
                update_auto_reproduce_locked(
                    entry, "build", (identity,), auto_reproduce=False, project_root=root
                )
                .commands["build"]
                .executions[identity]
            )
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

            updated = (
                clear_reproduction_requirement_locked(
                    entry, "build", identity, project_root=root
                )
                .commands["build"]
                .executions[identity]
            )
            self.assertFalse(updated.requires_reproduction)
            self.assertIsNone(updated.last_run_at)
            self.assertFalse(updated.auto_reproduce)

            retired = retire_execution_locked(
                entry, "build", identity, project_root=root
            )
            self.assertEqual(retired.commands, {})
            self.assertFalse((entry / PYRUN_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
