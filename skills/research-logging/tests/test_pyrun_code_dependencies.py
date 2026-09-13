from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pyrun_code_dependencies as dependencies


def _fixture(root: Path) -> tuple[Path, Path, Path]:
    log = root / "log"
    entry = log / "entries/2030-01-01-e001-static"
    scripts = entry / "scripts"
    scripts.mkdir(parents=True)
    return log, entry, scripts


def _relative_paths(
    discovery: dependencies.CodeDiscovery, entry: Path
) -> set[str]:
    return {
        path.logical.relative_to(entry).as_posix() for path in discovery.paths
    }


class StaticCodeDependencyTests(unittest.TestCase):
    def test_direct_transitive_imports_and_cycles_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, entry, scripts = _fixture(Path(directory))
            script = scripts / "main.py"
            script.write_text("import a\n", encoding="utf-8")
            (scripts / "a.py").write_text("import b\n", encoding="utf-8")
            (scripts / "b.py").write_text("import a\n", encoding="utf-8")

            result = dependencies.discover_code(
                script, entry_root=entry, log_root=log
            )

            self.assertEqual(
                _relative_paths(result, entry), {"scripts/a.py", "scripts/b.py"}
            )
            self.assertEqual(result.warnings, ())

    def test_package_and_relative_imports_include_initializers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, entry, scripts = _fixture(Path(directory))
            package = scripts / "package"
            package.mkdir()
            script = scripts / "main.py"
            script.write_text("from package.worker import VALUE\n", encoding="utf-8")
            (package / "__init__.py").write_text(
                "from .shared import VALUE\n", encoding="utf-8"
            )
            (package / "worker.py").write_text(
                "from .shared import VALUE\n", encoding="utf-8"
            )
            (package / "shared.py").write_text("VALUE = 1\n", encoding="utf-8")

            result = dependencies.discover_code(
                script, entry_root=entry, log_root=log
            )

            self.assertEqual(
                _relative_paths(result, entry),
                {
                    "scripts/package/__init__.py",
                    "scripts/package/shared.py",
                    "scripts/package/worker.py",
                },
            )
            self.assertEqual(result.warnings, ())

    def test_conditional_and_fallback_imports_are_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, entry, scripts = _fixture(Path(directory))
            script = scripts / "main.py"
            script.write_text(
                "from typing import TYPE_CHECKING\n"
                "if False:\n    import false_branch\n"
                "if TYPE_CHECKING:\n    import typing_only\n"
                "try:\n    import preferred\n"
                "except ImportError:\n    import fallback\n",
                encoding="utf-8",
            )
            for name in ("false_branch", "typing_only", "preferred", "fallback"):
                (scripts / f"{name}.py").write_text("VALUE = 1\n", encoding="utf-8")

            result = dependencies.discover_code(
                script, entry_root=entry, log_root=log
            )

            self.assertEqual(
                _relative_paths(result, entry),
                {
                    f"scripts/{name}.py"
                    for name in (
                        "false_branch",
                        "typing_only",
                        "preferred",
                        "fallback",
                    )
                },
            )

    def test_unresolved_and_dynamic_constructs_warn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, entry, scripts = _fixture(Path(directory))
            package = scripts / "package"
            package.mkdir()
            script = scripts / "main.py"
            script.write_text(
                "import importlib, subprocess, sys\n"
                "import package\n"
                "sys.path.insert(0, 'elsewhere')\n"
                "sys.path = ['replacement']\n"
                "importlib.import_module('dynamic')\n"
                "subprocess.run(['python', 'child.py'])\n"
                "exec('value = 1')\n",
                encoding="utf-8",
            )
            (package / "__init__.py").write_text(
                "from .missing import VALUE\n", encoding="utf-8"
            )

            result = dependencies.discover_code(
                script, entry_root=entry, log_root=log
            )

            self.assertEqual(
                _relative_paths(result, entry), {"scripts/package/__init__.py"}
            )
            self.assertEqual(
                {warning.code for warning in result.warnings},
                {
                    "descendant_code",
                    "dynamic_code",
                    "dynamic_import",
                    "import_path_mutation",
                    "relative_import_unresolved",
                },
            )

    def test_aliases_use_one_canonical_logical_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, entry, scripts = _fixture(Path(directory))
            script = scripts / "main.py"
            script.write_text("import helper\nimport alias\n", encoding="utf-8")
            helper = scripts / "helper.py"
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            (scripts / "alias.py").symlink_to(helper.name)

            result = dependencies.discover_code(
                script, entry_root=entry, log_root=log
            )

            self.assertEqual(_relative_paths(result, entry), {"scripts/alias.py"})

    def test_helper_limit_warns_and_keeps_a_deterministic_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, entry, scripts = _fixture(Path(directory))
            script = scripts / "main.py"
            script.write_text("import a\nimport b\nimport c\n", encoding="utf-8")
            for name in "abc":
                (scripts / f"{name}.py").write_text("VALUE = 1\n", encoding="utf-8")

            with mock.patch.object(dependencies, "MAX_CODE_PATHS", 2):
                result = dependencies.discover_code(
                    script, entry_root=entry, log_root=log
                )

            self.assertEqual(
                _relative_paths(result, entry), {"scripts/a.py", "scripts/b.py"}
            )
            self.assertIn("helper_limit", {warning.code for warning in result.warnings})

    def test_unparseable_helper_is_retained_with_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log, entry, scripts = _fixture(Path(directory))
            script = scripts / "main.py"
            script.write_text("import conditional\n", encoding="utf-8")
            (scripts / "conditional.py").write_text(
                "if True print('x')\n", encoding="utf-8"
            )

            result = dependencies.discover_code(
                script, entry_root=entry, log_root=log
            )

            self.assertEqual(
                _relative_paths(result, entry), {"scripts/conditional.py"}
            )
            self.assertIn(
                "parse_unsupported", {warning.code for warning in result.warnings}
            )


if __name__ == "__main__":
    unittest.main()
