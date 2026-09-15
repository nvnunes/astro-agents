"""Public Markdown-first directory, reference and evidence refresh workflow."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research_log_cli_test_support import run_log, run_pyrun_process
from test_log_command_sync import fixture, sync
from test_log_evidence_sync import evidence, retained_files, set_results
from validation.pyrun_state import load_pyrun_state


class RecordWorkflowTests(unittest.TestCase):
    def test_mixed_independent_table_keeps_unmarked_values_unaccepted(self):
        for unmarked in ("2", "`2`", "yes"):
            with (
                self.subTest(unmarked=unmarked),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                logical, entry, document = fixture(root, "./pyrun scripts/build.py")
                (entry / "data/value.json").write_text('{"value":1}')
                set_results(
                    document,
                    "Case | Marked | Missing\n--- | ---: | ---:\n"
                    "row | `1`<!-- eid:one source=values select=/value --> | "
                    + unmarked
                    + "\n",
                )
                result = evidence(
                    logical,
                    "sync",
                    "--id",
                    "one",
                    "--add-origin",
                    "values=data/value.json",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                result = run_log(
                    root,
                    "validate",
                    "run",
                    "--path",
                    str(logical),
                    "--format",
                    "json",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["outcome"], "findings")
                self.assertIn(
                    "Missing Evidence Declaration",
                    (logical / "validation.md").read_text(),
                )

    def test_directory_execution_and_cross_entry_evidence_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry, document = fixture(
                root,
                './pyrun scripts/build.py --input "<seed>" --output "<bundle>"',
            )
            seed = entry / "data/seed.txt"
            seed.write_text("2")
            (entry / "scripts/build.py").write_text(
                "import argparse, json\n"
                "from pathlib import Path\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--input', required=True)\n"
                "p.add_argument('--output', required=True)\n"
                "a = p.parse_args()\n"
                "value = int(Path(a.input).read_text())\n"
                "out = Path(a.output)\n"
                "out.mkdir(exist_ok=True)\n"
                "(out/'value.json').write_text(json.dumps({'value':value}))\n"
                "(out/'derived.csv').write_text(f'case,double\\none,{value*2}\\n')\n"
                "(out/'run.txt').write_text(f'completed {value}\\n')\n"
                "(out/'image.svg').write_text(f'<svg><!-- {value} --></svg>')\n"
            )
            (entry / "pyrun").symlink_to(
                Path(__file__).resolve().parents[1] / "scripts/pyrun"
            )
            result = sync(
                logical,
                "--add-origin",
                "seed=data/seed.txt",
                "--add-generated-directory",
                "bundle=data/bundle",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((entry / "data/bundle").exists())

            def execute():
                result = run_pyrun_process(
                    entry,
                    "--cid",
                    "build",
                    "--",
                    "scripts/build.py",
                    "--input",
                    "<seed>",
                    "--output",
                    "<bundle>",
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            execute()
            set_results(
                document,
                "``<!-- eid:value source=bundle/value.json select=/value -->\n\n"
                "<!-- eid:table source=bundle/derived.csv; column=/double "
                "parse=decimal render=integer; column=/case render=text -->\n"
                "Double | Case\n---: | ---\n\n"
                "<!-- eid:run source=bundle/run.txt line=1 -->\n```text\n```\n\n"
                "![Image](data/bundle/image.svg)"
                "<!-- eid:image source=bundle/image.svg -->",
            )
            for record_id in ("value", "table", "run", "image"):
                result = evidence(logical, "sync", "--id", record_id)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("4 | one", document.read_text())
            reference = logical / "entries/2030-01-02-e002-reference"
            reference.mkdir()
            ref_document = reference / "e002.md"
            ref_document.write_text(
                "# Reference\n\n## Result\n\n`Steps:`\n\nRead retained output.\n\n"
                "`Results:`\n\nName | Value\n--- | ---:\n"
                "answer | ``<!-- eid:answer source=bundle/value.json "
                "select=/value -->\n"
            )
            summary = logical.with_suffix(".md")
            summary.write_text(
                summary.read_text() + "- `2030-01-02` [Reference](study/entries/"
                "2030-01-02-e002-reference/e002.md)\n\n## Result\n\n"
                "`2`<!-- ref entry = e002; eid = answer -->\n"
            )
            result = run_log(
                root,
                "evidence",
                "sync",
                "--path",
                str(logical),
                "--entry",
                "e002",
                "--id",
                "answer",
                "--add-from-entry",
                "bundle=e001",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            support = entry / "data/pilot.txt"
            support.write_text("Retained runtime notes.\n")
            result = run_log(
                root,
                "retention",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "pilot",
                "--target",
                "data/pilot.txt",
                "--reason",
                "Runtime planning",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            seed.write_text("3")
            execute()
            state_before = (entry / "pyrun.json").read_bytes()
            records_before = json.loads((entry / "evidence.json").read_text())
            before = retained_files(logical)
            compared = evidence(logical, "compare", "--source", "bundle")
            self.assertEqual(compared.returncode, 0, compared.stderr)
            self.assertEqual(retained_files(logical), before)
            changes = {r["id"]: r for r in json.loads(compared.stdout)["records"]}
            self.assertEqual(changes["value"]["before"], "`2`")
            self.assertEqual(changes["value"]["after"], "`3`")
            self.assertEqual(changes["answer"]["after"], "`3`")
            self.assertEqual(changes["image"]["before"], changes["image"]["after"])
            result = evidence(logical, "sync", "--source", "bundle")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("6 | one", document.read_text())
            self.assertIn("completed 3", document.read_text())
            self.assertIn("answer | `3`", ref_document.read_text())
            self.assertIn("`3`<!-- ref", summary.read_text())
            records_after = json.loads((entry / "evidence.json").read_text())
            self.assertNotEqual(
                next(r for r in records_before["records"] if r["id"] == "image")[
                    "artifact_fingerprint"
                ],
                next(r for r in records_after["records"] if r["id"] == "image")[
                    "artifact_fingerprint"
                ],
            )
            self.assertEqual((entry / "pyrun.json").read_bytes(), state_before)
            command = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=root
            ).commands["build"]
            self.assertFalse(
                next(iter(command.executions.values())).requires_reproduction
            )
            accepted = retained_files(logical)
            result = evidence(logical, "sync", "--source", "bundle")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(retained_files(logical), accepted)
            result = run_log(
                root, "validate", "run", "--path", str(logical), "--format", "json"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout)["outcome"],
                "clear",
                (logical / "validation.md").read_text(),
            )
            self.assertEqual(support.read_text(), "Retained runtime notes.\n")
