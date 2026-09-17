from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from python_execution import PythonExecutionContext


class PythonExecutionContextTests(unittest.TestCase):
    def test_research_roots_are_specific_to_broad_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            log = project / "docs/topic"
            entry = log / "entries/2030-01-01-e001-example"
            script = entry / "scripts/build.py"
            script.parent.mkdir(parents=True)
            script.write_text("print('ok')\n", encoding="utf-8")

            context = PythonExecutionContext.for_research_script(
                script,
                entry_root=entry,
                log_root=log,
                project_root=project,
            )

            self.assertEqual(
                context.import_roots,
                (entry / "scripts", log / "scripts", project),
            )

    def test_environment_prepends_roots_and_preserves_external_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            log = project / "docs/topic"
            entry = log / "entries/2030-01-01-e001-example"
            script = log / "scripts/shared.py"
            script.parent.mkdir(parents=True)
            script.write_text("print('ok')\n", encoding="utf-8")
            external = project / "external"
            context = PythonExecutionContext.for_research_script(
                script,
                entry_root=entry,
                log_root=log,
                project_root=project,
            )

            environment = context.environment({"PYTHONPATH": str(external)})

            self.assertEqual(
                tuple(
                    Path(value)
                    for value in environment["PYTHONPATH"].split(os.pathsep)
                ),
                (log / "scripts", entry / "scripts", project, external),
            )

    def test_context_rejects_an_entry_from_another_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            script = project / "docs/one/scripts/shared.py"
            script.parent.mkdir(parents=True)
            script.write_text("print('ok')\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "does not belong"):
                PythonExecutionContext.for_research_script(
                    script,
                    entry_root=project / "docs/two/entries/2030-01-01-e001-example",
                    log_root=project / "docs/one",
                    project_root=project,
                )


if __name__ == "__main__":
    unittest.main()
