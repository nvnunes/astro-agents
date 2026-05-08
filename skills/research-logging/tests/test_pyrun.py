from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PYRUN = Path(__file__).resolve().parents[1] / "scripts" / "pyrun"


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, env=env)


def make_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path.resolve()


def make_entry(root: Path) -> Path:
    (root / "log.md").write_text("# Log\n", encoding="utf-8")
    entry = root / "log" / "entries" / "2026-05-01-e001-test-entry"
    (entry / "data").mkdir(parents=True)
    (entry / "scripts").mkdir()
    (entry / "data" / "input.csv").write_text("value\n1\n", encoding="utf-8")
    (entry / "data" / "index.csv").write_text(
        "name,type,location\ninput_csv,CSV,input.csv\n",
        encoding="utf-8",
    )
    (entry / "scripts" / "print_args.py").write_text(
        "import sys\nprint('\\n'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    return entry


class PyrunTests(unittest.TestCase):
    def test_resolves_project_log_and_data_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)

            result = run(
                [
                    sys.executable,
                    str(PYRUN),
                    "scripts/print_args.py",
                    "<project>",
                    "<log>",
                    "<input_csv>",
                    "series=<input_csv>/file.npz",
                ],
                cwd=entry,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual(lines[0], str(root))
            self.assertEqual(lines[1], str(root / "log"))
            self.assertEqual(lines[2], str(entry / "data" / "input.csv"))
            self.assertEqual(lines[3], f"series={entry / 'data' / 'input.csv'}/file.npz")

    def test_rejects_theme_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = make_repo(Path(directory))
            entry = make_entry(root)

            result = run([sys.executable, str(PYRUN), "scripts/print_args.py", "<theme>"], cwd=entry)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("<theme> is no longer supported", result.stderr)


if __name__ == "__main__":
    unittest.main()
