from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from log_commands.reproduction_comparison import (
    STAGING_SCHEMA,
    ArtifactComparison,
    ExecutionComparison,
    compare_artifacts,
    compare_execution_outputs,
    prepare_confirmation_updates_locked,
)
from log_commands.reproduction_execution import ExecutionAttempt, ExecutionCheckpoint
from research_log_data import Fingerprint
from test_log_reproduction_execution import _Fixture
from validation.pyrun_state import (
    ObservedExecution,
    PyrunFile,
    load_pyrun_state,
)


class ArtifactComparisonTests(unittest.TestCase):
    def test_opaque_bytes_match_and_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "model.pt"
            regenerated = root / "copy.pt"
            expected.write_bytes(b"model\x00bytes")
            regenerated.write_bytes(expected.read_bytes())

            matched = compare_artifacts(expected, regenerated)
            regenerated.write_bytes(b"different")
            changed = compare_artifacts(expected, regenerated)

            self.assertEqual(
                (matched.profile, matched.outcome), ("opaque_file", "matched")
            )
            self.assertEqual(changed.outcome, "changed")
            self.assertEqual(changed.reason, "content_changed")

    def test_text_requires_strict_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.txt"
            regenerated = root / "regenerated.txt"
            expected.write_bytes(b"\xff")
            regenerated.write_bytes(b"\xff")

            result = compare_artifacts(expected, regenerated)

            self.assertEqual(result.outcome, "comparison_failed")
            self.assertEqual(result.reason, "unsupported_format")

    def test_json_ignores_key_order_but_preserves_scalar_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.json"
            regenerated = root / "regenerated.json"
            expected.write_text('{"a":1,"b":[true,null]}')
            regenerated.write_text('{ "b": [true, null], "a": 1 }')

            matched = compare_artifacts(expected, regenerated)
            regenerated.write_text('{"a":1.0,"b":[true,null]}')
            changed = compare_artifacts(expected, regenerated)

            self.assertEqual(matched.outcome, "matched")
            self.assertEqual(changed.outcome, "changed")

    def test_table_preserves_typed_null_and_signed_zero_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.csv"
            regenerated = root / "regenerated.csv"
            expected.write_text("name,value,missing\na,1,\nb,-0.0,\n")
            regenerated.write_text("name,value,missing\na,+1,\nb,-0.0,\n")

            matched = compare_artifacts(expected, regenerated)
            regenerated.write_text("name,value,missing\na,+1,\nb,0.0,\n")
            changed = compare_artifacts(expected, regenerated)

            self.assertEqual(matched.outcome, "matched")
            self.assertEqual(changed.outcome, "changed")

    def test_table_preserves_header_text_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.csv"
            regenerated = root / "regenerated.csv"
            expected.write_text("1\nvalue\n")
            regenerated.write_text("+1\nvalue\n")

            result = compare_artifacts(expected, regenerated)

            self.assertEqual(result.outcome, "changed")

    def test_numpy_preserves_signed_zero_and_matches_nan_positions(self) -> None:
        import numpy as np

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.npy"
            regenerated = root / "regenerated.npy"
            np.save(expected, np.array([np.nan, -0.0, 2.0], dtype="f8"))
            np.save(regenerated, np.array([np.nan, -0.0, 2.0], dtype="f8"))

            matched = compare_artifacts(expected, regenerated)
            np.save(regenerated, np.array([np.nan, 0.0, 2.0], dtype="f8"))
            changed = compare_artifacts(expected, regenerated)

            self.assertEqual(matched.outcome, "matched")
            self.assertEqual(changed.outcome, "changed")

    def test_npz_compares_member_names_dtypes_and_values(self) -> None:
        import numpy as np

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.npz"
            regenerated = root / "regenerated.npz"
            np.savez(expected, values=np.array([1, 2], dtype="i4"))
            np.savez_compressed(regenerated, values=np.array([1, 2], dtype="i4"))

            matched = compare_artifacts(expected, regenerated)
            np.savez(regenerated, other=np.array([1, 2], dtype="i4"))
            changed = compare_artifacts(expected, regenerated)

            self.assertEqual(matched.outcome, "matched")
            self.assertEqual(changed.outcome, "changed")

    def test_hdf5_compares_structure_attributes_and_values(self) -> None:
        import h5py
        import numpy as np

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.h5"
            regenerated = root / "regenerated.h5"
            for path in (expected, regenerated):
                with h5py.File(path, "w") as handle:
                    handle.attrs["label"] = "fixture"
                    handle.create_dataset(
                        "values", data=np.array([np.nan, -0.0], dtype="f8")
                    )

            matched = compare_artifacts(expected, regenerated)
            with h5py.File(regenerated, "r+") as handle:
                handle["values"][1] = 0.0
            changed = compare_artifacts(expected, regenerated)

            self.assertEqual(matched.outcome, "matched")
            self.assertEqual(changed.outcome, "changed")

    def test_matlab_container_compares_named_members(self) -> None:
        import numpy as np
        from scipy.io import savemat

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.mat"
            regenerated = root / "regenerated.mat"
            savemat(expected, {"values": np.array([[1.0, np.nan]])})
            savemat(regenerated, {"values": np.array([[1.0, np.nan]])})

            matched = compare_artifacts(expected, regenerated)
            savemat(regenerated, {"values": np.array([[1.0, 3.0]])})
            changed = compare_artifacts(expected, regenerated)

            self.assertEqual(matched.outcome, "matched")
            self.assertEqual(changed.outcome, "changed")

    def test_images_compare_decoded_pixels_not_container_bytes(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.png"
            regenerated = root / "regenerated.png"
            image = Image.new("RGB", (3, 2), (10, 20, 30))
            image.save(expected, compress_level=0)
            image.save(regenerated, compress_level=9)

            result = compare_artifacts(expected, regenerated)

            self.assertNotEqual(expected.read_bytes(), regenerated.read_bytes())
            self.assertEqual(result.outcome, "matched")

    def test_directory_recurses_and_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected"
            regenerated = root / "regenerated"
            (expected / "nested").mkdir(parents=True)
            (regenerated / "nested").mkdir(parents=True)
            (expected / "nested" / "record.json").write_text('{"a":1}')
            (regenerated / "nested" / "record.json").write_text('{ "a": 1 }')

            matched = compare_artifacts(expected, regenerated)
            (regenerated / "link").symlink_to(regenerated / "nested" / "record.json")
            failed = compare_artifacts(expected, regenerated)

            self.assertEqual(matched.outcome, "matched")
            self.assertEqual(failed.outcome, "comparison_failed")
            self.assertEqual(failed.reason, "unsupported_format")


class ExecutionComparisonTests(unittest.TestCase):
    def test_wholly_matched_output_is_discarded_from_output_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            workspace = fixture.workspace()
            regenerated = workspace.map_source(fixture.output)
            regenerated.parent.mkdir(parents=True)
            regenerated.write_bytes(fixture.output.read_bytes())
            checkpoint = ExecutionCheckpoint(
                "e001", fixture.identity, "complete", "checkpoint.json", "now", ()
            )
            attempt = ExecutionAttempt(
                "e001",
                fixture.identity,
                0,
                False,
                None,
                None,
                checkpoint,
                (),
                "missing-stdout",
                "missing-stderr",
            )

            result = compare_execution_outputs(
                fixture.log, fixture.plan, workspace, attempt
            )

            self.assertTrue(result.matched)
            self.assertFalse(regenerated.exists())
            self.assertIsNone(result.staging)

    def test_changed_output_is_staged_without_changing_retained_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            retained = fixture.output.read_bytes()
            workspace = fixture.workspace()
            regenerated = workspace.map_source(fixture.output)
            regenerated.parent.mkdir(parents=True)
            regenerated.write_text("changed\n")
            stdout = workspace.diagnostics_root / "stdout.log"
            stderr = workspace.diagnostics_root / "stderr.log"
            stdout.write_text("out\n")
            stderr.write_text("err\n")
            checkpoint = ExecutionCheckpoint(
                "e001", fixture.identity, "complete", "checkpoint.json", "now", ()
            )
            attempt = ExecutionAttempt(
                "e001",
                fixture.identity,
                0,
                False,
                None,
                None,
                checkpoint,
                (),
                stdout.relative_to(workspace.run_root).as_posix(),
                stderr.relative_to(workspace.run_root).as_posix(),
            )

            result = compare_execution_outputs(
                fixture.log, fixture.plan, workspace, attempt
            )

            self.assertEqual(fixture.output.read_bytes(), retained)
            self.assertEqual(result.artifacts[0].outcome, "changed")
            self.assertIsNotNone(result.staging)
            staged = workspace.run_root / str(result.staging)
            self.assertEqual(
                (staged / "outputs" / "entry" / "data" / "result.txt").read_text(),
                "changed\n",
            )
            manifest = json.loads((workspace.run_root / "staging.json").read_text())
            self.assertEqual(manifest["schema"], STAGING_SCHEMA)
            self.assertEqual(manifest["run_id"], workspace.run_id)
            self.assertEqual(len(manifest["executions"]), 1)

    def test_partial_execution_stages_every_available_output_as_one_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            identity, second = _add_second_output(fixture)
            workspace = fixture.workspace()
            first_work = workspace.map_source(fixture.output)
            second_work = workspace.map_source(second)
            first_work.parent.mkdir(parents=True)
            first_work.write_text("partial first\n")
            second_work.write_text("partial second\n")
            checkpoint = ExecutionCheckpoint(
                "e001", identity, "partial", "checkpoint.json", None, ()
            )
            attempt = ExecutionAttempt(
                "e001",
                identity,
                1,
                False,
                "execution_failed",
                "failed",
                checkpoint,
                (),
                "missing-stdout",
                "missing-stderr",
            )

            result = compare_execution_outputs(
                fixture.log, fixture.plan, workspace, attempt
            )

            self.assertEqual(
                [item.outcome for item in result.artifacts], ["failed", "failed"]
            )
            staged = workspace.run_root / str(result.staging) / "outputs" / "entry"
            self.assertEqual(
                (staged / "data" / "result.txt").read_text(), "partial first\n"
            )
            self.assertEqual(
                (staged / "data" / "second.txt").read_text(), "partial second\n"
            )

    def test_one_change_stages_matching_siblings_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            identity, second = _add_second_output(fixture)
            workspace = fixture.workspace()
            first_work = workspace.map_source(fixture.output)
            second_work = workspace.map_source(second)
            first_work.parent.mkdir(parents=True)
            first_work.write_bytes(fixture.output.read_bytes())
            second_work.write_text("second changed\n")
            checkpoint = ExecutionCheckpoint(
                "e001", identity, "complete", "checkpoint.json", "now", ()
            )
            attempt = ExecutionAttempt(
                "e001",
                identity,
                0,
                False,
                None,
                None,
                checkpoint,
                (),
                "missing-stdout",
                "missing-stderr",
            )

            result = compare_execution_outputs(
                fixture.log, fixture.plan, workspace, attempt
            )

            self.assertEqual(
                [item.outcome for item in result.artifacts], ["matched", "changed"]
            )
            staged = workspace.run_root / str(result.staging) / "outputs" / "entry"
            self.assertEqual(
                (staged / "data" / "result.txt").read_text(), "retained\n"
            )
            self.assertEqual(
                (staged / "data" / "second.txt").read_text(), "second changed\n"
            )

    def test_confirmation_prepares_only_confirmed_bit_for_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), "print('unused')\n")
            before = (fixture.entry_root / "pyrun.json").read_text()
            artifact = ArtifactComparison(
                "data/result.txt",
                "matched",
                None,
                "text",
                Fingerprint("sha256", digest="a" * 64).as_dict(),
                Fingerprint("sha256", digest="a" * 64).as_dict(),
            )
            result = ExecutionComparison(
                "e001", fixture.identity, (artifact,), None, True
            )

            with mock.patch(
                "log_commands.reproduction_planner.verify_reproduction_snapshot"
            ) as verify:
                updates = prepare_confirmation_updates_locked(
                    fixture.log,
                    fixture.plan,
                    (result,),
                    project_root=fixture.project,
                )

            verify.assert_called_once_with(fixture.log, fixture.plan)
            self.assertEqual((fixture.entry_root / "pyrun.json").read_text(), before)
            self.assertEqual(len(updates.files), 1)
            self.assertEqual(updates.execution_ids, {"e001": {fixture.identity}})
            candidate = json.loads(next(iter(updates.files.values())))
            execution = candidate["executions"][fixture.identity]
            original = json.loads(before)["executions"][fixture.identity]
            self.assertTrue(execution["confirmed"])
            self.assertEqual(
                {key: value for key, value in execution.items() if key != "confirmed"},
                {key: value for key, value in original.items() if key != "confirmed"},
            )


def _fingerprint(path: Path) -> Fingerprint:
    import hashlib

    return Fingerprint("sha256", digest=hashlib.sha256(path.read_bytes()).hexdigest())


def _execution_id(execution: object) -> str:
    from validation.pyrun_state import PyrunExecution, execution_id

    assert isinstance(execution, PyrunExecution)
    return execution_id(execution.recipe)


def _add_second_output(fixture: _Fixture) -> tuple[str, Path]:
    second = fixture.entry_root / "data" / "second.txt"
    second.write_text("second retained\n")
    state = load_pyrun_state(
        fixture.entry_root / "pyrun.json",
        entry_root=fixture.entry_root,
        project_root=fixture.project,
    )
    execution = state.executions[fixture.identity]
    recipe = replace(
        execution.recipe,
        outputs=(
            ("data/result.txt", "file"),
            ("data/second.txt", "file"),
        ),
    )
    observed = ObservedExecution(
        execution.observed.script,
        execution.observed.inputs,
        execution.observed.code,
        (
            ("data/result.txt", execution.observed.outputs[0][1]),
            ("data/second.txt", _fingerprint(second)),
        ),
    )
    replacement = replace(execution, recipe=recipe, observed=observed)
    identity = _execution_id(replacement)
    state.path.write_text(
        PyrunFile(state.path, state.entry_root, {identity: replacement}).serialized()
    )
    return identity, second


if __name__ == "__main__":
    unittest.main()
