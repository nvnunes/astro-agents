from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from validation.output_bindings import OutputBindingError, project_output_bindings


def _entry(root: Path) -> Path:
    entry = root / "docs/log/entries/2030-01-01-e001-bindings"
    (entry / "data").mkdir(parents=True)
    return entry


class OutputBindingTests(unittest.TestCase):
    def test_projects_supported_parameter_forms_and_multiple_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            cases = (
                (
                    ("--output", "data/separate.csv"),
                    {"data/separate.csv": "file"},
                    1,
                    None,
                ),
                (
                    ("--destination=data/equals.csv",),
                    {"data/equals.csv": "file"},
                    0,
                    "--destination",
                ),
                (
                    ("--result", "data/nonstandard.csv"),
                    {"data/nonstandard.csv": "file"},
                    1,
                    None,
                ),
                (
                    ("data/positional.csv",),
                    {"data/positional.csv": "file"},
                    0,
                    None,
                ),
                (
                    ("--output", "<project>/generated/project.csv"),
                    {"<project>/generated/project.csv": "file"},
                    1,
                    None,
                ),
            )
            for parameters, outputs, index, prefix in cases:
                with self.subTest(parameters=parameters):
                    projection = project_output_bindings(
                        parameters,
                        outputs,
                        entry_root=entry,
                        project_root=root,
                    )

                    self.assertEqual(projection.child_parameters, parameters)
                    self.assertEqual(len(projection.parameters), 1)
                    self.assertEqual(
                        projection.parameters[0].parameter_index, index
                    )
                    self.assertEqual(
                        projection.parameters[0].equals_prefix, prefix
                    )
                    self.assertFalse(projection.captures)

            multiple = project_output_bindings(
                (
                    "--first=data/first.csv",
                    "--second",
                    "data/second.csv",
                ),
                {
                    "data/first.csv": "file",
                    "data/second.csv": "file",
                },
                entry_root=entry,
                project_root=root,
            )
            self.assertEqual(
                {binding.output for binding in multiple.bindings},
                {"data/first.csv", "data/second.csv"},
            )

    def test_projects_runner_owned_captures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)

            projection = project_output_bindings(
                (
                    "--capture-stdout",
                    "data/stdout.log",
                    "--capture-stderr",
                    "data/stderr.log",
                    "--",
                    "--mode",
                    "exact",
                ),
                {
                    "data/stderr.log": "file",
                    "data/stdout.log": "file",
                },
                entry_root=entry,
                project_root=root,
            )

            self.assertEqual(projection.child_parameters, ("--mode", "exact"))
            self.assertEqual(
                {binding.option for binding in projection.captures},
                {"--capture-stdout", "--capture-stderr"},
            )
            self.assertFalse(projection.parameters)

    def test_reports_missing_and_every_repeated_canonical_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            cases = (
                (
                    ("--mode", "exact"),
                    {"data/result.csv": "file"},
                    "missing",
                ),
                (
                    (
                        "--output",
                        "data/result.csv",
                        "--reference",
                        "data/result.csv",
                    ),
                    {"data/result.csv": "file"},
                    "ambiguous",
                ),
                (
                    (
                        "--output",
                        "data/result.csv",
                        "--reference",
                        "./data/result.csv",
                    ),
                    {"data/result.csv": "file"},
                    "ambiguous",
                ),
                (
                    (
                        "--capture-stdout",
                        "data/result.csv",
                        "--",
                        "--reference",
                        "data/result.csv",
                    ),
                    {"data/result.csv": "file"},
                    "ambiguous",
                ),
            )
            for parameters, outputs, reason in cases:
                with self.subTest(parameters=parameters), self.assertRaises(
                    OutputBindingError
                ) as captured:
                    project_output_bindings(
                        parameters,
                        outputs,
                        entry_root=entry,
                        project_root=root,
                    )
                self.assertEqual(captured.exception.observed["reason"], reason)

    def test_exposes_one_noncanonical_argument_or_capture_as_an_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            cases = (
                (
                    ("--output", "./data/result.csv"),
                    "parameter",
                ),
                (
                    (
                        "--capture-stdout-stderr",
                        "./data/result.csv",
                        "--",
                    ),
                    "capture",
                ),
            )
            for parameters, mechanism in cases:
                with self.subTest(mechanism=mechanism):
                    projection = project_output_bindings(
                        parameters,
                        {"data/result.csv": "file"},
                        entry_root=entry,
                        project_root=root,
                    )

                    self.assertEqual(len(projection.aliases), 1)
                    self.assertEqual(projection.aliases[0].mechanism, mechanism)
                    self.assertEqual(
                        projection.aliases[0].authored, "./data/result.csv"
                    )

    def test_rejects_invalid_capture_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = _entry(root)
            cases = (
                (
                    (
                        "--capture-stdout",
                        "data/result.csv",
                        "--capture-stdout",
                        "data/other.csv",
                        "--",
                    ),
                    {
                        "data/other.csv": "file",
                        "data/result.csv": "file",
                    },
                    "duplicate_capture_option",
                ),
                (
                    ("--capture-stdout", "data/result.csv", "--"),
                    {"data/result.csv": "directory"},
                    "capture_kind_invalid",
                ),
                (
                    ("--capture-stdout", "data/extra.log", "--"),
                    {"data/result.csv": "file"},
                    "capture_output_undeclared",
                ),
            )
            for parameters, outputs, reason in cases:
                with self.subTest(reason=reason), self.assertRaises(
                    OutputBindingError
                ) as captured:
                    project_output_bindings(
                        parameters,
                        outputs,
                        entry_root=entry,
                        project_root=root,
                    )
                self.assertEqual(captured.exception.observed["reason"], reason)


if __name__ == "__main__":
    unittest.main()
