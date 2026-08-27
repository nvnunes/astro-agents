from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

COMMANDS = importlib.import_module("validation.commands")


class PythonArgumentRoleTests(unittest.TestCase):
    def test_local_path_resolver_preserves_input_and_output_roles(self) -> None:
        source = '''
import argparse
from pathlib import Path
from scipy.io import loadmat

ROOT = Path(__file__).parent

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-mat", type=Path)
    parser.add_argument("--summary-csv", type=Path)
    parser.add_argument("--plot", type=Path)
    return parser.parse_args()

def resolve_entry_path(path):
    if path.is_absolute():
        return path
    return ROOT / path

def load_model(path):
    return loadmat(path)

def write_summary(path):
    path.open("w").close()

def write_plot(path):
    figure.savefig(path)

def main():
    args = parse_args()
    model = resolve_entry_path(args.model_mat)
    summary = resolve_entry_path(args.summary_csv)
    plot = resolve_entry_path(args.plot)
    load_model(model)
    write_summary(summary)
    write_plot(plot)
'''
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "analyze.py"
            script.write_text(source, encoding="utf-8")

            roles = COMMANDS.argparse_flags(script)["argument_roles"]

        self.assertEqual(
            roles,
            {
                "model_mat": "input",
                "plot": "output",
                "summary_csv": "output",
            },
        )


if __name__ == "__main__":
    unittest.main()
