from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import effective_code
from python_execution import PythonExecutionContext


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _fingerprint(
    script: Path, project: Path
) -> effective_code.EffectiveCodeFingerprint:
    result = effective_code.analyze_effective_code(script, project_root=project)
    if result.fingerprint is None:
        raise AssertionError(result.unsupported)
    return result.fingerprint


class EffectiveCodeTests(unittest.TestCase):
    def test_comment_format_and_unreachable_body_changes_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py",
                "def used():\n    return 1\n\n"
                "def unused():\n    return 2\n\n"
                "print(used())\n",
            )
            baseline = _fingerprint(script, project)

            script.write_text(
                "# comment\n\n"
                "def used( ):\n  return 1\n\n"
                "def unused():\n    return 999\n\n"
                "print( used() )\n",
                encoding="utf-8",
            )

            self.assertEqual(_fingerprint(script, project), baseline)

    def test_reachable_body_value_default_decorator_and_import_time_change(
        self,
    ) -> None:
        cases = {
            "body": (
                "def used():\n    return 1\nprint(used())\n",
                "def used():\n    return 2\nprint(used())\n",
            ),
            "value": ("VALUE = 1\nprint(VALUE)\n", "VALUE = 2\nprint(VALUE)\n"),
            "default": (
                "def used(value=1):\n    return value\nprint(used())\n",
                "def used(value=2):\n    return value\nprint(used())\n",
            ),
            "decorator": (
                "def decorate(fn):\n    return fn\n"
                "@decorate\ndef used():\n    return 1\nprint(used())\n",
                "def decorate(fn):\n    return fn\n"
                "@decorate()\ndef used():\n    return 1\nprint(used())\n",
            ),
            "import_time": ("TOKEN = object()\n", "TOKEN = object(); OTHER = 1\n"),
        }
        for label, (before, after) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                script = _write(project / "script.py", before)
                baseline = _fingerprint(script, project)
                script.write_text(after, encoding="utf-8")
                self.assertNotEqual(_fingerprint(script, project), baseline)

    def test_project_local_import_outside_log_participates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "docs/log/entries/e001/scripts/main.py",
                "from shared.helper import answer\nprint(answer())\n",
            )
            helper = _write(
                project / "shared/helper.py",
                "def answer():\n    return 1\n",
            )
            _write(project / "shared/__init__.py", "")
            baseline = _fingerprint(script, project)
            helper.write_text("def answer():\n    return 2\n", encoding="utf-8")
            self.assertNotEqual(_fingerprint(script, project), baseline)

    def test_log_shared_import_uses_the_runner_owned_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            log = project / "docs/topic"
            entry = log / "entries/2030-01-01-e001-example"
            script = _write(
                entry / "scripts/main.py",
                "from shared_helper import answer\nprint(answer())\n",
            )
            helper = _write(
                log / "scripts/shared_helper.py",
                "def answer():\n    return 1\n",
            )
            context = PythonExecutionContext.for_research_script(
                script,
                entry_root=entry,
                log_root=log,
                project_root=project,
            )
            baseline = effective_code.analyze_effective_code(
                script,
                project_root=project,
                import_roots=context.import_roots,
            ).fingerprint
            self.assertIsNotNone(baseline)

            helper.write_text("def answer():\n    return 2\n", encoding="utf-8")

            self.assertNotEqual(
                effective_code.analyze_effective_code(
                    script,
                    project_root=project,
                    import_roots=context.import_roots,
                ).fingerprint,
                baseline,
            )

    def test_import_resolution_preserves_runner_root_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            log = project / "docs/topic"
            entry = log / "entries/2030-01-01-e001-example"
            script = _write(
                entry / "scripts/main.py",
                "import entry_bridge\nprint(entry_bridge.answer())\n",
            )
            entry_helper = _write(
                entry / "scripts/shared_helper.py",
                "def answer():\n    return 1\n",
            )
            log_helper = _write(
                log / "scripts/shared_helper.py",
                "def answer():\n    return 2\n",
            )
            _write(
                entry / "scripts/entry_bridge.py",
                "from shared_helper import answer\n",
            )
            context = PythonExecutionContext.for_research_script(
                script,
                entry_root=entry,
                log_root=log,
                project_root=project,
            )
            baseline = effective_code.analyze_effective_code(
                script,
                project_root=project,
                import_roots=context.import_roots,
            ).fingerprint
            self.assertIsNotNone(baseline)

            log_helper.write_text("def answer():\n    return 3\n", encoding="utf-8")
            self.assertEqual(
                effective_code.analyze_effective_code(
                    script,
                    project_root=project,
                    import_roots=context.import_roots,
                ).fingerprint,
                baseline,
            )
            entry_helper.write_text("def answer():\n    return 4\n", encoding="utf-8")
            self.assertNotEqual(
                effective_code.analyze_effective_code(
                    script,
                    project_root=project,
                    import_roots=context.import_roots,
                ).fingerprint,
                baseline,
            )

    def test_project_local_namespace_package_participates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py",
                "from namespace.helper import answer\nprint(answer())\n",
            )
            helper = _write(
                project / "namespace/helper.py",
                "def answer():\n    return 1\n",
            )
            baseline = _fingerprint(script, project)
            helper.write_text("def answer():\n    return 2\n", encoding="utf-8")
            self.assertNotEqual(_fingerprint(script, project), baseline)

    def test_unrelated_module_and_external_implementation_do_not_participate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py", "import json\nprint(json.dumps(1))\n"
            )
            unrelated = _write(project / "unrelated.py", "VALUE = 1\n")
            baseline = _fingerprint(script, project)
            unrelated.write_text("VALUE = 2\n", encoding="utf-8")
            self.assertEqual(_fingerprint(script, project), baseline)

    def test_alias_callbacks_resolved_methods_and_cycles_participate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py",
                "import worker as w\n"
                "from worker import callback as cb\n"
                "item = w.Worker()\n"
                "item.run(cb)\n",
            )
            worker = _write(
                project / "worker.py",
                "import cycle\n"
                "def callback():\n    return cycle.VALUE\n"
                "class Worker:\n"
                "    def run(self, fn):\n        return fn()\n"
                "    def unused(self):\n        return 9\n",
            )
            _write(project / "cycle.py", "import worker\nVALUE = 1\n")
            baseline = _fingerprint(script, project)
            worker.write_text(
                "import cycle\n"
                "def callback():\n    return cycle.VALUE\n"
                "class Worker:\n"
                "    def run(self, fn):\n        return fn() + 1\n"
                "    def unused(self):\n        return 9\n",
                encoding="utf-8",
            )
            self.assertNotEqual(_fingerprint(script, project), baseline)

    def test_inline_constructor_method_and_callback_participate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py",
                "class Worker:\n"
                "    def result(self):\n"
                "        return 1\n"
                "print(Worker().result())\n"
                "callback = Worker().result\n"
                "print(callback())\n",
            )
            baseline = _fingerprint(script, project)

            script.write_text(
                script.read_text(encoding="utf-8").replace("return 1", "return 2"),
                encoding="utf-8",
            )

            self.assertNotEqual(_fingerprint(script, project), baseline)

    def test_raw_only_script_change_preserves_effective_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(project / "script.py", "print(1)\n")
            raw_before = script.read_bytes()
            semantic_before = _fingerprint(script, project)
            script.write_text("# new comment\nprint( 1 )\n", encoding="utf-8")
            self.assertNotEqual(script.read_bytes(), raw_before)
            self.assertEqual(_fingerprint(script, project), semantic_before)

    def test_unresolved_methods_may_terminate_at_an_external_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py",
                "def run():\n"
                "    from framework import ExternalBase\n"
                "    class LocalBase(ExternalBase):\n"
                "        pass\n"
                "    class Model(LocalBase):\n"
                "        def __init__(self, value):\n"
                "            self.register_buffer('value', value)\n"
                "        def evaluate(self):\n"
                "            return self.parameters()\n"
                "    model = Model(1)\n"
                "    model.to('cpu')\n"
                "    print(model.evaluate())\n"
                "run()\n",
            )
            baseline = _fingerprint(script, project)

            script.write_text(
                script.read_text(encoding="utf-8").replace("'cpu'", "'cuda'"),
                encoding="utf-8",
            )

            self.assertNotEqual(_fingerprint(script, project), baseline)

    def test_dynamic_base_does_not_hide_unresolved_project_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py",
                "def make_base():\n"
                "    return object\n"
                "class Local(make_base()):\n"
                "    pass\n"
                "Local().missing()\n",
            )

            result = effective_code.analyze_effective_code(
                script, project_root=project
            )

            self.assertIsNone(result.fingerprint)
            self.assertIn(
                "project_dispatch_unresolved",
                {item.construct for item in result.unsupported},
            )

    def test_dynamic_base_prevents_external_base_dispatch_exemption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py",
                "from framework import ExternalBase\n"
                "class ProjectBase:\n"
                "    def render(self):\n"
                "        return 1\n"
                "def choose():\n"
                "    return ProjectBase\n"
                "class Composite(choose(), ExternalBase):\n"
                "    pass\n"
                "Composite().render()\n",
            )

            result = effective_code.analyze_effective_code(
                script, project_root=project
            )

            self.assertIsNone(result.fingerprint)
            self.assertIn(
                "project_dispatch_unresolved",
                {item.construct for item in result.unsupported},
            )

    def test_unsupported_families_return_no_partial_fingerprint(self) -> None:
        cases = (
            ("dynamic_import", "import importlib\nimportlib.import_module('x')\n"),
            ("dynamic_code", "exec('value = 1')\n"),
            ("import_path_mutation", "import sys\nsys.path.append('x')\n"),
            ("import_path_mutation", "import sys\nsys.path = ['x']\n"),
            ("wildcard_import", "from math import *\n"),
            (
                "project_dispatch_unresolved",
                "class Local:\n    pass\nLocal().missing()\n",
            ),
            (
                "child_entrypoint_unresolved",
                "import subprocess\nsubprocess.run(['python', 'missing.py'])\n",
            ),
        )
        for expected, source in cases:
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                project = Path(directory)
                result = effective_code.analyze_effective_code(
                    _write(project / "script.py", source), project_root=project
                )
                self.assertIsNone(result.fingerprint)
                self.assertIn(expected, {item.construct for item in result.unsupported})

    def test_unsupported_standard_library_aliases_cannot_bypass_detection(self) -> None:
        cases = (
            (
                "dynamic_import",
                "import importlib as il\nil.import_module('x')\n",
            ),
            (
                "dynamic_import",
                "from importlib import import_module as load\nload('x')\n",
            ),
            (
                "dynamic_code",
                "from builtins import exec as run\nrun('value = 1')\n",
            ),
            (
                "import_path_mutation",
                "from sys import path as search\nsearch.append('x')\n",
            ),
            (
                "child_entrypoint_unresolved",
                "import subprocess as sp\nimport sys\n"
                "sp.run([sys.executable, 'missing.py'])\n",
            ),
        )
        for expected, source in cases:
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                project = Path(directory)
                result = effective_code.analyze_effective_code(
                    _write(project / "script.py", source), project_root=project
                )
                self.assertIsNone(result.fingerprint)
                self.assertIn(expected, {item.construct for item in result.unsupported})

    def test_static_project_local_child_entrypoint_participates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py",
                "import subprocess\nsubprocess.run(['python', 'child.py'])\n",
            )
            child = _write(project / "child.py", "print(1)\n")
            baseline = _fingerprint(script, project)
            child.write_text("print(2)\n", encoding="utf-8")
            self.assertNotEqual(_fingerprint(script, project), baseline)

    def test_static_self_child_entrypoint_uses_dunder_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py",
                "import subprocess\n"
                "import sys\n"
                "if '--child' not in sys.argv:\n"
                "    subprocess.run([sys.executable, __file__, '--child'])\n",
            )

            self.assertIsNotNone(_fingerprint(script, project))

    def test_unsupported_locations_are_sorted_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py",
                "exec('a')\nexec('b')\nexec('c')\n",
            )
            with mock.patch.object(effective_code, "MAX_UNSUPPORTED_LOCATIONS", 2):
                result = effective_code.analyze_effective_code(
                    script, project_root=project
                )
            self.assertEqual([item.line for item in result.unsupported], [1, 2])
            self.assertTrue(result.unsupported_truncated)

    def test_syntax_size_count_and_outside_project_fail_operationally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            invalid = _write(project / "invalid.py", "if True print('x')\n")
            with self.assertRaisesRegex(
                effective_code.EffectiveCodeError, "effective_code.syntax_invalid"
            ):
                effective_code.analyze_effective_code(invalid, project_root=project)

            large = _write(project / "large.py", "x = 1\n")
            with mock.patch.object(effective_code, "MAX_SOURCE_BYTES", 1):
                with self.assertRaisesRegex(
                    effective_code.EffectiveCodeError,
                    "effective_code.source_size_limit",
                ):
                    effective_code.analyze_effective_code(large, project_root=project)

            _write(project / "helper.py", "VALUE = 1\n")
            count = _write(project / "count.py", "import helper\n")
            with mock.patch.object(effective_code, "MAX_SOURCE_FILES", 1):
                with self.assertRaisesRegex(
                    effective_code.EffectiveCodeError, "effective_code.source_limit"
                ):
                    effective_code.analyze_effective_code(count, project_root=project)

            outside = _write(project.parent / f"{project.name}-outside.py", "pass\n")
            try:
                with self.assertRaisesRegex(
                    effective_code.EffectiveCodeError,
                    "effective_code.source_outside_project",
                ):
                    effective_code.analyze_effective_code(outside, project_root=project)
            finally:
                outside.unlink()

    def test_analysis_does_not_import_or_execute_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            marker = project / "executed"
            script = _write(
                project / "script.py",
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
            )
            self.assertIsNotNone(_fingerprint(script, project))
            self.assertFalse(marker.exists())

    def test_supported_and_unsupported_analysis_leave_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            supported = _write(project / "supported.py", "print(1)\n")
            unsupported = _write(project / "unsupported.py", "exec('value = 1')\n")
            before = {
                path.relative_to(project).as_posix(): path.read_bytes()
                for path in project.rglob("*")
                if path.is_file()
            }
            self.assertIsNotNone(
                effective_code.analyze_effective_code(
                    supported, project_root=project
                ).fingerprint
            )
            self.assertIsNone(
                effective_code.analyze_effective_code(
                    unsupported, project_root=project
                ).fingerprint
            )
            after = {
                path.relative_to(project).as_posix(): path.read_bytes()
                for path in project.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_fingerprint_is_stable_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = _write(
                project / "script.py", "def f():\n    return 1\nprint(f())\n"
            )
            module_root = Path(effective_code.__file__).parent
            program = (
                "import json,sys; from pathlib import Path; "
                "from effective_code import analyze_effective_code; "
                "r=analyze_effective_code(Path(sys.argv[1]),"
                "project_root=Path(sys.argv[2])); "
                "print(json.dumps(r.fingerprint.__dict__,sort_keys=True))"
            )
            outputs = {
                subprocess.check_output(
                    [sys.executable, "-c", program, str(script), str(project)],
                    env={"PYTHONPATH": str(module_root)},
                    text=True,
                ).strip()
                for _ in range(3)
            }
            self.assertEqual(len(outputs), 1)
            self.assertEqual(
                json.loads(outputs.pop())["algorithm"],
                effective_code.FINGERPRINT_ALGORITHM,
            )


if __name__ == "__main__":
    unittest.main()
