from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from test_pyrun import PYRUN, execution_for_output, make_entry, make_repo, run


class PyrunEffectiveCodeTests(unittest.TestCase):
    def test_supported_tree_ignores_comments_and_tracks_reachable_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            helper = entry / "scripts/helper.py"
            script = entry / "scripts/build.py"
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            script.write_text(
                "from pathlib import Path\n"
                "import helper\n"
                "Path('data/result.txt').write_text(str(helper.VALUE))\n",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(PYRUN),
                "scripts/build.py",
                "--output-data",
                "data/result.txt",
            ]

            first = run(command, cwd=entry)
            self.assertEqual(first.returncode, 0, first.stderr)
            initial = execution_for_output(entry, "data/result.txt")["observed"][
                "effective_code"
            ]
            helper.write_text("# comment\nVALUE = 1\n", encoding="utf-8")
            comment_only = run(command, cwd=entry)
            self.assertEqual(comment_only.returncode, 0, comment_only.stderr)
            unchanged = execution_for_output(entry, "data/result.txt")["observed"][
                "effective_code"
            ]
            self.assertEqual(initial, unchanged)

            helper.write_text("VALUE = 2\n", encoding="utf-8")
            changed = run(command, cwd=entry)
            self.assertEqual(changed.returncode, 0, changed.stderr)
            current = execution_for_output(entry, "data/result.txt")["observed"][
                "effective_code"
            ]
            self.assertNotEqual(initial, current)

    def test_unsupported_source_executes_silently_and_records_null(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            (entry / "scripts/helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            (entry / "scripts/build.py").write_text(
                "import importlib\n"
                "from pathlib import Path\n"
                "helper = importlib.import_module('helper')\n"
                "Path('data/result.txt').write_text(str(helper.VALUE))\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/build.py",
                    "--output-data",
                    "data/result.txt",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertIsNone(
                execution_for_output(entry, "data/result.txt")["observed"][
                    "effective_code"
                ]
            )

    def test_log_shared_helper_import_executes_and_is_fingerprinted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            log_scripts = entry.parent.parent / "scripts"
            log_scripts.mkdir()
            helper = log_scripts / "shared_helper.py"
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            (entry / "scripts/build.py").write_text(
                "from pathlib import Path\n"
                "from shared_helper import VALUE\n"
                "Path('data/result.txt').write_text(str(VALUE))\n",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                str(PYRUN),
                "scripts/build.py",
                "--output-data",
                "data/result.txt",
            ]

            first = run(command, cwd=entry)
            self.assertEqual(first.returncode, 0, first.stderr)
            baseline = execution_for_output(entry, "data/result.txt")["observed"][
                "effective_code"
            ]
            self.assertIsNotNone(baseline)
            self.assertEqual((entry / "data/result.txt").read_text(), "1")

            helper.write_text("VALUE = 2\n", encoding="utf-8")
            second = run(command, cwd=entry)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((entry / "data/result.txt").read_text(), "2")
            self.assertNotEqual(
                execution_for_output(entry, "data/result.txt")["observed"][
                    "effective_code"
                ],
                baseline,
            )

    def test_entry_helper_wins_over_same_named_log_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)
            log_scripts = entry.parent.parent / "scripts"
            log_scripts.mkdir()
            (log_scripts / "shared_helper.py").write_text(
                "VALUE = 'log'\n", encoding="utf-8"
            )
            (entry / "scripts/shared_helper.py").write_text(
                "VALUE = 'entry'\n", encoding="utf-8"
            )
            (entry / "scripts/entry_bridge.py").write_text(
                "from shared_helper import VALUE\n", encoding="utf-8"
            )
            (entry / "scripts/build.py").write_text(
                "from pathlib import Path\n"
                "from entry_bridge import VALUE\n"
                "Path('data/result.txt').write_text(VALUE)\n",
                encoding="utf-8",
            )

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/build.py",
                    "--output-data",
                    "data/result.txt",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((entry / "data/result.txt").read_text(), "entry")
            self.assertIsNotNone(
                execution_for_output(entry, "data/result.txt")["observed"][
                    "effective_code"
                ]
            )


if __name__ == "__main__":
    unittest.main()
