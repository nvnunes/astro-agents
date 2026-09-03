from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import validation.pyrun_outputs as PYRUN_OUTPUTS
from research_log_data import Fingerprint
from research_log_validation_test_support import mock, unittest, write
from validation.pyrun_outputs import (
    OutputSupport,
    PyrunOutputsError,
    ScriptSupport,
    load_pyrun_outputs,
    update_pyrun_outputs,
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class PyrunOutputsContractTests(unittest.TestCase):
    def test_output_key_is_unique_portable_identity_and_not_repeated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            write(entry / "data/output.csv", "value\n1\n")
            record = OutputSupport(
                True,
                Fingerprint("sha256", digest=digest(b"value\n1\n")),
                ScriptSupport(
                    "scripts/run.py", Fingerprint("sha256", digest="a" * 64)
                ),
                ("--output-data", "data/output.csv"),
                (),
            )

            result = update_pyrun_outputs(entry, {"data/output.csv": record})

            raw = json.loads(result.path.read_text())
            self.assertEqual(list(raw["outputs"]), ["data/output.csv"])
            self.assertNotIn("output", raw["outputs"]["data/output.csv"])
            self.assertEqual(
                load_pyrun_outputs(result.path, entry_root=entry).outputs[
                    "data/output.csv"
                ],
                record,
            )

    def test_parameter_vector_preserves_an_empty_argument(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            entry.mkdir()
            record = OutputSupport(
                True,
                Fingerprint("sha256", digest="a" * 64),
                ScriptSupport("scripts/run.py", Fingerprint("sha256", digest="b" * 64)),
                ("--label", ""),
                (),
            )

            written = update_pyrun_outputs(entry, {"data/output.csv": record})

            loaded = load_pyrun_outputs(written.path, entry_root=entry)
            self.assertEqual(
                loaded.outputs["data/output.csv"].parameters,
                ("--label", ""),
            )

    def test_update_replaces_only_the_named_output_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            entry.mkdir()
            first = OutputSupport(
                True,
                Fingerprint("sha256", digest="a" * 64),
                ScriptSupport("scripts/run.py", Fingerprint("sha256", digest="b" * 64)),
                ("--mode", "old"),
                (),
            )
            second = OutputSupport(
                True,
                Fingerprint("sha256", digest="c" * 64),
                ScriptSupport("scripts/run.py", Fingerprint("sha256", digest="b" * 64)),
                ("--mode", "old"),
                (),
            )
            update_pyrun_outputs(
                entry,
                {"data/first.csv": first, "data/second.csv": second},
            )
            replacement = OutputSupport(
                True,
                Fingerprint("sha256", digest="d" * 64),
                ScriptSupport(
                    "scripts/first.py",
                    Fingerprint("sha256", digest="e" * 64),
                ),
                ("--mode", "new"),
                (),
            )

            result = update_pyrun_outputs(
                entry,
                {"data/first.csv": replacement},
            )

            self.assertEqual(result.outputs["data/first.csv"], replacement)
            self.assertEqual(result.outputs["data/second.csv"], second)

    def test_duplicate_json_output_key_is_rejected_before_construction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            entry.mkdir()
            path = entry / "pyrun-outputs.json"
            record = (
                '{"confirmed":true,"fingerprint":{"algorithm":"sha256",'
                '"digest":"' + "a" * 64 + '"},"inputs":{},"parameters":["x"],'
                '"script":{"path":"run.py","fingerprint":{"algorithm":'
                '"sha256","digest":"' + "b" * 64 + '"}}}'
            )
            path.write_text(
                '{"schema":"research-log-pyrun-outputs/v1","outputs":{'
                '"data/a.csv":' + record + ',"data/a.csv":' + record + "}}",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PyrunOutputsError, "duplicate JSON key"):
                load_pyrun_outputs(path, entry_root=entry)

    def test_output_identity_must_belong_to_data_or_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            entry.mkdir()
            payload = {
                "schema": "research-log-pyrun-outputs/v1",
                "outputs": {
                    "notes.txt": {
                        "confirmed": False,
                        "fingerprint": {"algorithm": "sha256", "digest": "a" * 64},
                        "inputs": {},
                        "parameters": ["--output", "notes.txt"],
                        "script": {
                            "path": "run.py",
                            "fingerprint": {
                                "algorithm": "sha256",
                                "digest": "b" * 64,
                            },
                        },
                    }
                },
            }
            path = entry / "pyrun-outputs.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(PyrunOutputsError, "not_entry_material"):
                load_pyrun_outputs(path, entry_root=entry)

    def test_writer_rejects_a_record_the_reader_cannot_accept(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            entry.mkdir()
            valid = OutputSupport(
                True,
                Fingerprint("sha256", digest="a" * 64),
                ScriptSupport("scripts/run.py", Fingerprint("sha256", digest="b" * 64)),
                ("--mode", "valid"),
                (),
            )
            path = update_pyrun_outputs(entry, {"data/output.csv": valid}).path
            before = path.read_bytes()
            invalid = OutputSupport(
                True,
                Fingerprint("sha256", digest="a" * 64),
                ScriptSupport("scripts/run.py", Fingerprint("sha256", digest="b" * 64)),
                tuple("value" for _ in range(PYRUN_OUTPUTS.MAX_PARAMETERS + 1)),
                (),
            )

            with self.assertRaisesRegex(PyrunOutputsError, "pyrun.outputs.invalid"):
                update_pyrun_outputs(entry, {"data/output.csv": invalid})

            self.assertEqual(path.read_bytes(), before)

            too_many_inputs = OutputSupport(
                True,
                Fingerprint("sha256", digest="a" * 64),
                ScriptSupport("scripts/run.py", Fingerprint("sha256", digest="b" * 64)),
                (),
                tuple(
                    (f"input{index}", Fingerprint("sha256", digest="c" * 64))
                    for index in range(PYRUN_OUTPUTS.MAX_INPUTS + 1)
                ),
            )
            overlong_parameter = OutputSupport(
                True,
                Fingerprint("sha256", digest="a" * 64),
                ScriptSupport("scripts/run.py", Fingerprint("sha256", digest="b" * 64)),
                ("x" * (PYRUN_OUTPUTS.MAX_STRING_BYTES + 1),),
                (),
            )
            for record in (too_many_inputs, overlong_parameter):
                with self.subTest(record=record):
                    with self.assertRaisesRegex(
                        PyrunOutputsError, "pyrun.outputs.invalid"
                    ):
                        update_pyrun_outputs(entry, {"data/output.csv": record})
                    self.assertEqual(path.read_bytes(), before)

    def test_writer_enforces_merged_output_and_serialized_size_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entry"
            entry.mkdir()
            record = OutputSupport(
                True,
                Fingerprint("sha256", digest="a" * 64),
                ScriptSupport("scripts/run.py", Fingerprint("sha256", digest="b" * 64)),
                (),
                (),
            )
            path = update_pyrun_outputs(entry, {"data/first.csv": record}).path
            before = path.read_bytes()

            with mock.patch.object(PYRUN_OUTPUTS, "MAX_OUTPUTS", 1):
                with self.assertRaisesRegex(PyrunOutputsError, "pyrun.outputs.invalid"):
                    update_pyrun_outputs(entry, {"data/second.csv": record})
            self.assertEqual(path.read_bytes(), before)

            size_entry = Path(directory) / "size-entry"
            size_entry.mkdir()
            with mock.patch.object(PYRUN_OUTPUTS, "MAX_FILE_BYTES", 1):
                with self.assertRaisesRegex(PyrunOutputsError, "pyrun.outputs.invalid"):
                    update_pyrun_outputs(size_entry, {"data/first.csv": record})
            self.assertFalse((size_entry / "pyrun-outputs.json").exists())


if __name__ == "__main__":
    unittest.main()
