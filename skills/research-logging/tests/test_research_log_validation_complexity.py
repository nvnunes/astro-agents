"""Regression tests for the identity-preserving complexity ratchet."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


def _complexity_module():
    path = (
        Path(__file__).parents[3] / "scripts" / "check_research_logging_complexity.py"
    )
    spec = importlib.util.spec_from_file_location("complexity_ratchet", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load complexity-ratchet module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMPLEXITY = _complexity_module()


class ComplexityRatchetTests(unittest.TestCase):
    def test_new_finding_cannot_replace_removed_baseline_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "module.py"
            source.write_text(
                "def retained(a, b, c, d, e, f):\n    return a\n\n"
                "def replacement(a, b, c, d, e, f):\n    return b\n",
                encoding="utf-8",
            )
            findings = [
                {
                    "filename": str(source),
                    "location": {"row": 4},
                    "code": "PLR0913",
                    "message": "Too many arguments in function definition (6 > 5)",
                }
            ]
            expected = {"module.py:retained:PLR0913": 6}

            issues, current = COMPLEXITY._ratchet_issues(findings, expected, root)

            self.assertEqual(current, {"module.py:replacement:PLR0913": 6})
            self.assertEqual(
                issues,
                ["new complexity finding: module.py:replacement:PLR0913 (6)"],
            )

    def test_existing_finding_cannot_increase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "module.py"
            source.write_text(
                "def retained(a, b, c, d, e, f, g):\n    return a\n",
                encoding="utf-8",
            )
            finding = {
                "filename": str(source),
                "location": {"row": 1},
                "code": "PLR0913",
                "message": "Too many arguments in function definition (7 > 5)",
            }

            issues, _current = COMPLEXITY._ratchet_issues(
                [finding], {"module.py:retained:PLR0913": 6}, root
            )

            self.assertEqual(
                issues,
                [
                    "complexity finding grew: module.py:retained:PLR0913 "
                    "(7 > 6)"
                ],
            )


if __name__ == "__main__":
    unittest.main()
