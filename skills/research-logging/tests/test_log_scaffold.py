from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
LOG = SCRIPT_ROOT / "log"
PYRUN = SCRIPT_ROOT / "pyrun"
sys.path.insert(0, str(SCRIPT_ROOT))

from log_commands import scaffold  # noqa: E402
from log_commands.context import resolve_log, resolve_log_creation  # noqa: E402
from log_commands.model import ActionError, AddArguments, InitArguments  # noqa: E402


def run(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    return subprocess.run(
        [sys.executable, str(LOG), *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


def payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout)


def project(root: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    docs = root / "docs"
    docs.mkdir()
    return docs / "study"


def initialize(root: Path, logical: Path | None = None) -> Path:
    target = logical or project(root)
    result = run(
        root,
        "init",
        "--path",
        str(target),
        "--title",
        "Calibration Study",
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return target


def add(
    root: Path,
    logical: Path,
    *,
    date: str = "2026-09-04",
    title: str = "Detector drift",
    slug: str = "detector-drift",
) -> subprocess.CompletedProcess[str]:
    return run(
        root,
        "add",
        "--path",
        str(logical),
        "--date",
        date,
        "--title",
        title,
        "--slug",
        slug,
    )


class LogScaffoldHelpTests(unittest.TestCase):
    def test_progressive_help_exposes_only_selected_depth(self) -> None:
        top = run(Path.cwd(), "--help")
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertIn("init", top.stdout)
        self.assertIn("add", top.stdout)
        self.assertNotIn("--title", top.stdout)

        init = run(Path.cwd(), "init", "--help")
        self.assertEqual(init.returncode, 0, init.stderr)
        self.assertIn("--title", init.stdout)
        self.assertNotIn("--date", init.stdout)

        add_help = run(Path.cwd(), "add", "--help")
        self.assertEqual(add_help.returncode, 0, add_help.stderr)
        self.assertIn("--date", add_help.stdout)
        self.assertIn("--slug", add_help.stdout)
        self.assertNotIn("--source", add_help.stdout)

    def test_help_does_not_import_scaffolding_implementation(self) -> None:
        code = f"""
import json
import sys
sys.path.insert(0, {str(SCRIPT_ROOT)!r})
from log_commands.dispatcher import main
for arguments in ([\"--help\"], [\"init\", \"--help\"], [\"add\", \"--help\"]):
    try:
        main(arguments)
    except SystemExit as error:
        assert error.code == 0
print(json.dumps({{"scaffold": "log_commands.scaffold" in sys.modules}}))
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.splitlines()[-1]), {"scaffold": False}
        )


class LogInitTests(unittest.TestCase):
    def test_init_dry_run_then_creates_only_canonical_empty_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = project(root)
            dry = run(
                root,
                "init",
                "--path",
                str(logical),
                "--title",
                "Calibration Study",
                "--dry-run",
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(payload(dry)["status"], "dry-run")
            self.assertFalse(logical.exists())
            self.assertFalse(logical.with_suffix(".md").exists())

            initialized = initialize(root, logical)
            self.assertEqual(initialized, logical)
            self.assertEqual(list(logical.iterdir()), [logical / "entries"])
            self.assertEqual(list((logical / "entries").iterdir()), [])
            summary = logical.with_suffix(".md").read_text(encoding="utf-8")
            self.assertTrue(summary.startswith("# Calibration Study\n\nValidation: "))
            self.assertIn("\n## Entries\n\n## Summary\n", summary)
            self.assertTrue(summary.endswith(scaffold.AI_DISCLOSURE + "\n"))

    def test_init_conflict_and_partial_residue_fail_without_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = initialize(root)
            before = logical.with_suffix(".md").read_bytes()
            conflict = run(
                root,
                "init",
                "--path",
                str(logical),
                "--title",
                "Calibration Study",
            )
            self.assertEqual(conflict.returncode, 2)
            self.assertEqual(payload(conflict)["code"], "log.scaffold.conflict")
            self.assertEqual(logical.with_suffix(".md").read_bytes(), before)

        for summary_only in (False, True):
            with self.subTest(summary_only=summary_only):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    logical = project(root)
                    if summary_only:
                        logical.with_suffix(".md").write_text("partial\n")
                    else:
                        logical.mkdir()
                    result = run(
                        root,
                        "init",
                        "--path",
                        str(logical),
                        "--title",
                        "Calibration Study",
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(payload(result)["code"], "log.scaffold.residue")

    def test_init_rejects_noncanonical_paths_and_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = project(root)
            cases = (
                (str(logical.with_suffix(".md")), "Calibration Study"),
                (str(logical.parent / "entries"), "Calibration Study"),
                (str(logical), " bad"),
                (str(logical), "bad]title"),
            )
            for path, title in cases:
                with self.subTest(path=path, title=title):
                    result = run(
                        root,
                        "init",
                        "--path",
                        path,
                        "--title",
                        title,
                    )
                    self.assertEqual(result.returncode, 2)
            self.assertFalse(logical.exists())
            self.assertFalse(logical.with_suffix(".md").exists())

    def test_log_path_with_spaces_uses_safe_markdown_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = project(root).with_name("study log")
            initialize(root, logical)
            result = add(root, logical)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = logical.with_suffix(".md").read_text(encoding="utf-8")
            self.assertIn("(<study log/validation.md>)", summary)
            self.assertIn(
                "(<study log/entries/2026-09-04-e001-detector-drift/e001.md>)",
                summary,
            )

    def test_init_rolls_back_each_ordinary_publication_failure(self) -> None:
        boundaries = ("root", "entries", "summary")
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    logical = project(root)
                    context = resolve_log_creation(logical, cwd=root)
                    real_mkdir = scaffold._make_directory
                    calls = 0

                    def mkdir(path: Path, created: list[Path]) -> None:
                        nonlocal calls
                        calls += 1
                        if boundary == "root" and calls == 1:
                            raise OSError("injected root failure")
                        real_mkdir(path, created)
                        if boundary == "entries" and calls == 2:
                            raise OSError("injected entries failure")

                    summary_error = (
                        mock.patch.object(
                            scaffold,
                            "atomic_create_text",
                            side_effect=OSError("injected summary failure"),
                        )
                        if boundary == "summary"
                        else mock.patch.object(
                            scaffold,
                            "atomic_create_text",
                            wraps=scaffold.atomic_create_text,
                        )
                    )
                    with mock.patch.object(
                        scaffold, "_make_directory", side_effect=mkdir
                    ):
                        with summary_error:
                            with self.assertRaises(ActionError) as raised:
                                scaffold.initialize(
                                    context,
                                    InitArguments("Calibration Study", False),
                                )
                    self.assertEqual(raised.exception.code, "init.failed")
                    self.assertFalse(logical.exists())
                    self.assertFalse(logical.with_suffix(".md").exists())


class LogAddTests(unittest.TestCase):
    def test_add_allocates_entries_and_installs_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = initialize(root)
            first = add(root, logical)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(payload(first)["task"], "add")
            entry = logical / "entries" / "2026-09-04-e001-detector-drift"
            self.assertEqual(
                (entry / "e001.md").read_text(encoding="utf-8"),
                "# 2026-09-04: Detector drift\n",
            )
            self.assertTrue((entry / "pyrun").is_symlink())
            self.assertEqual((entry / "pyrun").resolve(), PYRUN.resolve())
            self.assertEqual(
                {path.name for path in entry.iterdir()}, {"e001.md", "pyrun"}
            )

            second = add(
                root,
                logical,
                date="2026-09-05",
                title="Noise floor",
                slug="noise-floor",
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertTrue(
                (logical / "entries" / "2026-09-05-e002-noise-floor").is_dir()
            )
            summary = logical.with_suffix(".md").read_text(encoding="utf-8")
            self.assertLess(summary.index("e001.md"), summary.index("e002.md"))

    def test_add_ignores_non_entry_files_in_entries_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = initialize(root)
            incidental = logical / "entries" / ".DS_Store"
            incidental.write_bytes(b"incidental\n")
            result = add(root, logical)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(incidental.read_bytes(), b"incidental\n")

    def test_add_supports_split_entries_and_never_fills_id_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = initialize(root)
            entry = logical / "entries" / "2026-09-01-e003-existing"
            entry.mkdir()
            (entry / "e003a.md").write_text("# A\n", encoding="utf-8")
            (entry / "e003b.md").write_text("# B\n", encoding="utf-8")
            summary = logical.with_suffix(".md")
            text = summary.read_text(encoding="utf-8")
            text = text.replace(
                "## Entries\n\n",
                "## Entries\n\n"
                "- `2026-09-01` Existing:\n"
                "  - [A](study/entries/2026-09-01-e003-existing/e003a.md)\n"
                "  - [B](study/entries/2026-09-01-e003-existing/e003b.md)\n\n",
            )
            summary.write_text(text, encoding="utf-8")

            result = add(root, logical)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                (logical / "entries" / "2026-09-04-e004-detector-drift").is_dir()
            )

    def test_add_preserves_summary_and_generated_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = initialize(root)
            summary = logical.with_suffix(".md")
            customized = summary.read_text(encoding="utf-8").replace(
                "## Summary\n\n", "## Summary\n\n- Existing interpretation.\n\n"
            )
            summary.write_text(customized, encoding="utf-8")
            generated = {
                logical / "validation.md": b"human\n",
                logical / "validation" / "mechanical.json": b"machine\n",
            }
            for path, value in generated.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)

            result = add(root, logical)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = customized.replace(
                "## Entries\n\n",
                "## Entries\n\n"
                "- `2026-09-04` [Detector drift]"
                "(study/entries/2026-09-04-e001-detector-drift/e001.md)\n\n",
            )
            self.assertEqual(summary.read_text(encoding="utf-8"), expected)
            self.assertEqual({path: path.read_bytes() for path in generated}, generated)

    def test_add_dry_run_conflicts_and_invalid_arguments_do_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = initialize(root)
            summary = logical.with_suffix(".md")
            before = summary.read_bytes()
            dry = run(
                root,
                "add",
                "--path",
                str(logical),
                "--date",
                "2026-09-04",
                "--title",
                "Detector drift",
                "--slug",
                "detector-drift",
                "--dry-run",
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(payload(dry)["status"], "dry-run")
            self.assertEqual(list((logical / "entries").iterdir()), [])
            self.assertEqual(summary.read_bytes(), before)

            invalid = (
                ("2026-02-30", "Detector drift", "detector-drift"),
                ("2026-09-04", " bad", "detector-drift"),
                ("2026-09-04", "Detector drift", "Detector_Drift"),
            )
            for date, title, slug in invalid:
                result = add(root, logical, date=date, title=title, slug=slug)
                self.assertEqual(result.returncode, 2)
            self.assertEqual(list((logical / "entries").iterdir()), [])
            self.assertEqual(summary.read_bytes(), before)

            self.assertEqual(add(root, logical).returncode, 0)
            conflict = add(root, logical, title="Different title")
            self.assertEqual(conflict.returncode, 2)
            self.assertEqual(payload(conflict)["code"], "entry.scaffold.conflict")

    def test_add_refuses_identity_residue_without_completing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = initialize(root)
            partial = logical / "entries" / "2026-09-04-e001-detector-drift"
            partial.mkdir()
            (partial / "e001.md").write_text("# partial\n", encoding="utf-8")
            result = add(root, logical)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload(result)["code"], "entry.scaffold.residue")
            self.assertTrue(partial.is_dir())
            self.assertFalse((partial / "pyrun").exists())

    def test_add_rolls_back_every_publication_boundary(self) -> None:
        boundaries = ("directory", "document", "runner", "summary")
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    logical = initialize(root)
                    summary = logical.with_suffix(".md")
                    before = summary.read_bytes()
                    context = resolve_log(logical, cwd=root)
                    if boundary == "directory":
                        patcher = mock.patch.object(
                            scaffold,
                            "_make_directory",
                            side_effect=OSError("injected directory failure"),
                        )
                    elif boundary == "document":
                        patcher = mock.patch.object(
                            scaffold,
                            "atomic_create_text",
                            side_effect=OSError("injected document failure"),
                        )
                    elif boundary == "runner":
                        patcher = mock.patch.object(
                            scaffold,
                            "create_symlink",
                            side_effect=OSError("injected runner failure"),
                        )
                    else:
                        patcher = mock.patch.object(
                            scaffold,
                            "atomic_write_text",
                            side_effect=OSError("injected summary failure"),
                        )
                    with patcher:
                        with self.assertRaises(ActionError) as raised:
                            scaffold.add_entry(
                                context,
                                AddArguments(
                                    "2026-09-04",
                                    "Detector drift",
                                    "detector-drift",
                                    False,
                                ),
                            )
                    self.assertEqual(raised.exception.code, "add.failed")
                    self.assertEqual(summary.read_bytes(), before)
                    self.assertEqual(list((logical / "entries").iterdir()), [])

    def test_add_restores_summary_when_failure_follows_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = initialize(root)
            summary = logical.with_suffix(".md")
            before = summary.read_bytes()
            context = resolve_log(logical, cwd=root)
            real_write = scaffold.atomic_write_text
            calls = 0

            def write_then_fail(path: Path, text: str) -> None:
                nonlocal calls
                calls += 1
                real_write(path, text)
                if calls == 1:
                    raise OSError("injected post-replace failure")

            with mock.patch.object(
                scaffold, "atomic_write_text", side_effect=write_then_fail
            ):
                with self.assertRaises(ActionError) as raised:
                    scaffold.add_entry(
                        context,
                        AddArguments(
                            "2026-09-04",
                            "Detector drift",
                            "detector-drift",
                            False,
                        ),
                    )
            self.assertEqual(raised.exception.code, "add.failed")
            self.assertEqual(summary.read_bytes(), before)
            self.assertEqual(list((logical / "entries").iterdir()), [])


class LogScaffoldConcurrencyTests(unittest.TestCase):
    def test_competing_init_and_add_calls_serialize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical = project(root)
            init_command = [
                sys.executable,
                str(LOG),
                "init",
                "--path",
                str(logical),
                "--title",
                "Calibration Study",
            ]
            init_processes = [
                subprocess.Popen(
                    init_command,
                    cwd=root,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for _ in range(2)
            ]
            init_results = [
                process.communicate(timeout=5) for process in init_processes
            ]
            self.assertEqual(
                sorted(process.returncode for process in init_processes), [0, 2]
            )
            self.assertTrue(all(stdout for stdout, _ in init_results))

            commands = (
                [
                    sys.executable,
                    str(LOG),
                    "add",
                    "--path",
                    str(logical),
                    "--date",
                    "2026-09-04",
                    "--title",
                    "Detector drift",
                    "--slug",
                    "detector-drift",
                ],
                [
                    sys.executable,
                    str(LOG),
                    "add",
                    "--path",
                    str(logical),
                    "--date",
                    "2026-09-05",
                    "--title",
                    "Noise floor",
                    "--slug",
                    "noise-floor",
                ],
            )
            processes = [
                subprocess.Popen(
                    command,
                    cwd=root,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                for command in commands
            ]
            results = [process.communicate(timeout=5) for process in processes]
            self.assertEqual([process.returncode for process in processes], [0, 0])
            self.assertTrue(all(stdout for stdout, _ in results))
            folders = sorted(path.name for path in (logical / "entries").iterdir())
            self.assertEqual(len(folders), 2)
            self.assertEqual({name.split("-")[3] for name in folders}, {"e001", "e002"})
            summary = logical.with_suffix(".md").read_text(encoding="utf-8")
            self.assertLess(summary.index("e001.md"), summary.index("e002.md"))


if __name__ == "__main__":
    unittest.main()
