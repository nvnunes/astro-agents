from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_log_cli_test_support import run_log, run_log_process

LOG = Path(__file__).resolve().parents[1] / "scripts" / "log"
PYRUN = Path(__file__).resolve().parents[1] / "scripts" / "pyrun"


def run(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run_log(cwd, *arguments)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_input(entry: Path, name: str, source: Path) -> None:
    path = entry / "data.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["inputs"].append(
        {
            "fingerprint": {"algorithm": "sha256", "digest": digest(source)},
            "kind": "file",
            "location": source.relative_to(entry).as_posix(),
            "name": name,
            "origin": True,
        }
    )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def authoring_result(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout)


def write_definition(directory: Path, value: object) -> Path:
    path = directory / "definition.json"
    if isinstance(value, bytes):
        path.write_bytes(value)
    elif isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    return path


def fixture(root: Path) -> tuple[Path, Path]:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    summary = root / "docs" / "study.md"
    entry = root / "docs" / "study" / "entries" / "2026-09-03-e001-study"
    (entry / "data").mkdir(parents=True)
    (entry / "scripts").mkdir()
    summary.write_text(
        "# Study\n\n"
        "Validation: [latest completed report](study/validation.md)\n\n"
        "## Summary\n\n"
        "## Entries\n\n"
        "- `2026-09-03` [Study](study/entries/2026-09-03-e001-study/e001.md)\n",
        encoding="utf-8",
    )
    document = entry / "e001.md"
    document.write_text(
        "# Study\n\n## Trial\n\n`Results:`\n\n"
        "The rate was `67.6%`<!-- eid:success-rate -->.\n\n"
        "<!-- eid:comparison -->\n"
        "Case | Value\n--- | ---\ncandidate | exact\n\n"
        "<!-- eid:run-output -->\n```text\ncompleted\n```\n",
        encoding="utf-8",
    )
    results = entry / "data" / "results.csv"
    results.write_text("case,rate,value\ncandidate,0.676,exact\n", encoding="utf-8")
    run_log = entry / "data" / "run.log"
    run_log.write_text("completed\n", encoding="utf-8")
    (entry / "data.json").write_text(
        json.dumps(
            {
                "schema": "research-log-data/v3",
                "inputs": [
                    {
                        "fingerprint": {
                            "algorithm": "sha256",
                            "digest": digest(results),
                        },
                        "kind": "file",
                        "location": "data/results.csv",
                        "name": "results",
                        "origin": True,
                    },
                    {
                        "fingerprint": {
                            "algorithm": "sha256",
                            "digest": digest(run_log),
                        },
                        "kind": "file",
                        "location": "data/run.log",
                        "name": "run-log",
                        "origin": True,
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary.with_suffix(""), entry


class LogHelpAndContextTests(unittest.TestCase):
    def test_progressive_help_lists_only_current_depth(self) -> None:
        top = run(Path.cwd(), "--help")
        self.assertEqual(top.returncode, 0, top.stderr)
        for family_name in (
            "add",
            "data",
            "discover",
            "evidence",
            "init",
            "reorganize",
            "retention",
            "validate",
        ):
            self.assertIn(family_name, top.stdout)
        self.assertNotIn("--source", top.stdout)

        family = run(Path.cwd(), "evidence", "--help")
        self.assertEqual(family.returncode, 0, family.stderr)
        self.assertIn("add", family.stdout)
        self.assertNotIn("--source", family.stdout)

        action = run(Path.cwd(), "evidence", "add", "--help")
        self.assertEqual(action.returncode, 0, action.stderr)
        self.assertIn("--source", action.stdout)
        self.assertIn("--definition", action.stdout)
        self.assertIn("Author the unique presentation marker", action.stdout)
        self.assertIn("JSON Pointer", action.stdout)
        self.assertIn("logical log base", action.stdout)

        retention = run(Path.cwd(), "retention", "add", "--help")
        self.assertEqual(retention.returncode, 0, retention.stderr)
        self.assertIn("disconnected-retention decision", retention.stdout)
        self.assertIn("one directory or one or more", retention.stdout)

    def test_invalid_authoring_arguments_emit_a_structured_failure(self) -> None:
        failed = run(Path.cwd(), "evidence", "add")

        self.assertEqual(failed.returncode, 2)
        payload = authoring_result(failed)
        self.assertEqual(payload["task"], "evidence.add")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["code"], "cli.arguments.invalid")
        self.assertIn("cli.arguments.invalid", failed.stderr)

    def test_help_and_selected_family_imports_are_lazy(self) -> None:
        script_root = LOG.parent
        code = f"""
import json
import sys
sys.path.insert(0, {str(script_root)!r})
from log_commands.dispatcher import main
for arguments in (
    [\"evidence\", \"--help\"],
    [\"evidence\", \"add\", \"--help\"],
    [\"data\", \"--help\"],
    [\"data\", \"add-origin\", \"--help\"],
    [\"reorganize\", \"--help\"],
    [\"reorganize\", \"transfer\", \"--help\"],
    [\"init\", \"--help\"],
    [\"add\", \"--help\"],
):
    try:
        main(arguments)
    except SystemExit as error:
        assert error.code == 0
print(json.dumps({{
    \"evidence\": \"log_commands.evidence\" in sys.modules,
    \"retention\": \"log_commands.retention\" in sys.modules,
    \"data\": \"log_commands.data\" in sys.modules,
    \"materials\": \"log_commands.materials\" in sys.modules,
    \"reorganize\": \"log_commands.reorganize\" in sys.modules,
    \"transfer\": \"log_commands.reorganize_transfer\" in sys.modules,
    \"scaffold\": \"log_commands.scaffold\" in sys.modules,
    \"validation\": \"validation.controller\" in sys.modules,
    \"validation_engine\": \"validation.engine\" in sys.modules,
    \"numpy\": \"numpy\" in sys.modules,
}}))
"""
        result = subprocess.run(
            [sys.executable, "-c", code], text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.splitlines()[-1]),
            {
                "evidence": False,
                "retention": False,
                "data": False,
                "materials": False,
                "reorganize": False,
                "transfer": False,
                "scaffold": False,
                "validation": False,
                "validation_engine": False,
                "numpy": False,
            },
        )

    def test_path_rejects_summary_and_entries_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            for path in (logical.with_suffix(".md"), logical / "entries"):
                result = run(
                    entry,
                    "retention",
                    "list",
                    "--path",
                    str(path),
                    "--entry",
                    "e001",
                )
                self.assertNotEqual(result.returncode, 0)

    def test_entry_resolution_uses_only_the_canonical_identity_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            malformed = entry.with_name("2026-09-03-e001-e002-study")
            entry.rename(malformed)

            canonical = run(
                malformed,
                "retention",
                "list",
                "--path",
                str(logical),
                "--entry",
                "e001",
            )
            alias = run(
                malformed,
                "retention",
                "list",
                "--path",
                str(logical),
                "--entry",
                "e002",
            )
            self.assertEqual(canonical.returncode, 0, canonical.stderr)
            self.assertEqual(alias.returncode, 2)
            self.assertEqual(
                authoring_result(alias)["code"], "entry.identity.unresolved"
            )

    def test_context_inference_requires_exactly_one_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            inferred = run(entry, "retention", "list", "--entry", "e001")
            self.assertEqual(inferred.returncode, 0, inferred.stderr)
            self.assertEqual(authoring_result(inferred)["records"], [])

            nested = entry / "nested"
            nested_entry = nested / "entries" / "2026-09-04-e002-nested"
            nested_entry.mkdir(parents=True)
            (entry / "nested.md").write_text(
                "# Nested\n\n"
                "Validation: [latest completed report](nested/validation.md)\n",
                encoding="utf-8",
            )
            ambiguous = run(
                nested_entry, "retention", "list", "--entry", "e002"
            )
            self.assertEqual(ambiguous.returncode, 2)
            self.assertEqual(
                authoring_result(ambiguous)["code"], "log.context.ambiguous"
            )
            self.assertTrue(logical.is_dir())

    def test_explicit_log_selects_its_project_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            caller = root / "caller"
            target.mkdir()
            caller.mkdir()
            logical, _ = fixture(target)
            subprocess.run(["git", "init"], cwd=caller, check=True, capture_output=True)
            target_marker = target / "selected"
            caller_marker = caller / "selected"
            for project, marker in (
                (target, target_marker),
                (caller, caller_marker),
            ):
                executable = project / ".conda" / "bin" / "python"
                executable.parent.mkdir(parents=True)
                executable.write_text(
                    "#!/bin/sh\n"
                    f"printf selected > {str(marker)!r}\n"
                    f"exec {str(Path(sys.executable))!r} \"$@\"\n",
                    encoding="utf-8",
                )
                executable.chmod(0o755)

            result = run_log_process(
                caller,
                "retention",
                "list",
                "--path",
                str(logical),
                "--entry",
                "e001",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target_marker.is_file())
            self.assertFalse(caller_marker.exists())


class LogValidationRouteTests(unittest.TestCase):
    def test_public_routes_preserve_one_log_discovery_and_batch_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, _ = fixture(root)
            common = ("--date", "2026-09-03", "--dry-run", "--recompute")
            current = run(root, "validate", "--path", str(logical), *common)
            self.assertEqual(current.returncode, 0, current.stderr)
            current_payload = json.loads(current.stdout)
            self.assertEqual(current_payload["status"], "complete_findings")
            self.assertFalse(current_payload["published"])

            new_discovery = run(root, "discover", "--root", str(root))
            self.assertEqual(new_discovery.returncode, 0, new_discovery.stderr)
            self.assertEqual(
                json.loads(new_discovery.stdout)["summaries"],
                [logical.with_suffix(".md").resolve().as_posix()],
            )

            batch = run(root, "validate", "--root", str(root), *common)
            self.assertEqual(batch.returncode, current.returncode, batch.stderr)
            batch_payload = json.loads(batch.stdout)
            batch_result = batch_payload["results"][0]
            self.assertEqual(
                set(batch_result["metrics"]),
                set(json.loads(current.stdout)["metrics"]),
            )
            batch_result.pop("metrics")
            expected = json.loads(current.stdout)
            expected.pop("metrics")
            self.assertEqual(batch_payload["results"], [expected])
            self.assertEqual(
                batch_payload["schema"], "research-log-validation-batch-result/1"
            )
            self.assertEqual(batch_payload["failures"], [])
            self.assertIn(
                "| Research log | Structure Failures | Evidence Failures |",
                batch_payload["report"],
            )
            self.assertIn(
                f"[Study](<{logical.with_suffix('.md').resolve()}>)",
                batch_payload["report"],
            )
            self.assertIn("Not published", batch_payload["report"])

    def test_validation_does_not_load_mutation_families(self) -> None:
        script_root = LOG.parent
        code = f"""
import json
import sys
sys.path.insert(0, {str(script_root)!r})
from log_commands.dispatcher import main
try:
    main([\"validate\", \"--path\", \"missing\", \"--dry-run\"])
except Exception:
    pass
print(json.dumps({{
    \"evidence\": \"log_commands.evidence\" in sys.modules,
    \"retention\": \"log_commands.retention\" in sys.modules,
}}))
"""
        result = subprocess.run(
            [sys.executable, "-c", code], text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.splitlines()[-1]),
            {"evidence": False, "retention": False},
        )


class LogLockTests(unittest.TestCase):
    def test_exclusive_log_lock_rejects_entry_tools_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, entry = fixture(root)
            source = entry / "data" / "new.txt"
            source.write_text("new\n", encoding="utf-8")
            script = entry / "scripts" / "noop.py"
            script.write_text("pass\n", encoding="utf-8")
            operation_dir = logical / ".cache" / "research-log-operations"
            operation_dir.mkdir(parents=True)
            lock = operation_dir / "log.lock"
            tracked = (
                logical.with_suffix(".md"),
                entry / "e001.md",
                entry / "data.json",
            )
            before = {
                path: path.read_bytes()
                for path in tracked
            }

            with lock.open("a+b") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                authoring = run_log_process(
                    entry,
                    "data",
                    "add-origin",
                    "--path",
                    str(logical),
                    "--entry",
                    "e001",
                    "new",
                    "data/new.txt",
                )
                publishing = run_log_process(
                    root, "validate", "--path", str(logical)
                )
                dry_run = run_log_process(
                    root, "validate", "--path", str(logical), "--dry-run"
                )
                runner = subprocess.run(
                    [sys.executable, str(PYRUN), "scripts/noop.py"],
                    cwd=entry,
                    text=True,
                    capture_output=True,
                    check=False,
                )

            for result in (authoring, publishing, dry_run):
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("operation", result.stderr)
            self.assertEqual(runner.returncode, 1, runner.stderr)
            self.assertIn("operation conflict", runner.stderr)
            self.assertEqual(
                {
                    path: path.read_bytes()
                    for path in tracked
                },
                before,
            )
            self.assertFalse((logical / "validation.md").exists())

    def test_process_termination_releases_operation_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, _ = fixture(Path(directory))
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(LOG.parent)
            code = """
import sys
from pathlib import Path
from validation.operation_state import operation_lock
with operation_lock(Path(sys.argv[1]), 'log.lock'):
    print('ready', flush=True)
    sys.stdin.readline()
"""
            holder = subprocess.Popen(
                [sys.executable, "-u", "-c", code, str(logical)],
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            self.assertIsNotNone(holder.stdout)
            self.assertEqual(holder.stdout.readline().strip(), "ready")
            holder.terminate()
            holder.communicate(timeout=2)
            acquired = subprocess.run(
                [sys.executable, "-u", "-c", code, str(logical)],
                input="\n",
                text=True,
                capture_output=True,
                env=environment,
                timeout=2,
                check=False,
            )
            self.assertEqual(acquired.returncode, 0, acquired.stderr)
            self.assertEqual(acquired.stdout.strip(), "ready")

    def test_entry_lock_excludes_same_entry_but_not_distinct_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, _ = fixture(Path(directory))
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(LOG.parent)
            holder_code = """
import sys
from pathlib import Path
from validation.operation_state import operation_lock
with operation_lock(Path(sys.argv[1]), 'entry-e001.lock'):
    print('ready', flush=True)
    sys.stdin.readline()
"""
            acquire_code = """
import sys
from pathlib import Path
from validation.operation_state import operation_lock
with operation_lock(Path(sys.argv[1]), sys.argv[2]):
    print('acquired', flush=True)
"""
            holder = subprocess.Popen(
                [sys.executable, "-u", "-c", holder_code, str(logical)],
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            self.assertIsNotNone(holder.stdout)
            self.assertEqual(holder.stdout.readline().strip(), "ready")
            distinct = subprocess.run(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    acquire_code,
                    str(logical),
                    "entry-e002.lock",
                ],
                text=True,
                capture_output=True,
                env=environment,
                timeout=2,
                check=False,
            )
            self.assertEqual(distinct.stdout.strip(), "acquired", distinct.stderr)
            same = subprocess.run(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    acquire_code,
                    str(logical),
                    "entry-e001.lock",
                ],
                text=True,
                capture_output=True,
                env=environment,
                timeout=2,
                check=False,
            )
            self.assertNotEqual(same.returncode, 0)
            self.assertIn("research-log operation is active", same.stderr)
            self.assertIsNotNone(holder.stdin)
            holder.stdin.write("\n")
            holder.stdin.flush()
            holder.communicate(timeout=2)


class LogEvidenceTests(unittest.TestCase):
    def test_artifact_accepts_one_exact_registered_directory_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            image = entry / "images" / "maps" / "map.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"map bytes")
            common = ("--path", str(logical), "--entry", "e001")
            registered = run(
                entry,
                "data",
                "add-origin",
                *common,
                "artifacts",
                "images/maps",
                "--identity",
                "map.png",
            )
            self.assertEqual(registered.returncode, 0, registered.stderr)
            document = entry / "e001.md"
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n## Image\n\n`Background:`\n\nInspect the map.\n\n"
                "`Steps:`\n\nOpen the image.\n\n`Results:`\n\n"
                "![Map](images/maps/map.png)<!-- eid:map-image -->\n",
                encoding="utf-8",
            )

            added = run(
                entry,
                "evidence",
                "add",
                *common,
                "--id",
                "map-image",
                "--source",
                "<artifacts>/map.png",
            )

            self.assertEqual(added.returncode, 0, added.stderr)
            record = json.loads((entry / "evidence.json").read_text())["records"][0]
            self.assertEqual(record["sources"][0]["source"], "<artifacts>/map.png")

    def test_whole_artifact_round_trip_and_path_association(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            artifact = entry / "data" / "residual map.png"
            artifact.write_bytes(b"retained image bytes")
            duplicate = entry / "data" / "duplicate.png"
            duplicate.write_bytes(artifact.read_bytes())
            add_input(entry, "residual-map", artifact)
            add_input(entry, "duplicate", duplicate)
            document = entry / "e001.md"
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n## Artifact\n\n`Background:`\n\nInspect the map.\n\n"
                "`Steps:`\n\nOpen the retained image.\n\n`Results:`\n\n"
                "![Residual map](<data/residual%20map.png>)"
                "<!-- eid:residual-map -->\n",
                encoding="utf-8",
            )
            common = ("--path", str(logical), "--entry", "e001")
            arguments = (
                "evidence",
                "add",
                *common,
                "--id",
                "residual-map",
                "--source",
                "residual-map",
            )

            dry_run = run(entry, *arguments, "--dry-run")
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertFalse((entry / "evidence.json").exists())
            added = run(entry, *arguments)
            self.assertEqual(added.returncode, 0, added.stderr)
            record = json.loads((entry / "evidence.json").read_text())["records"][0]
            self.assertEqual(record["kind"], "artifact")
            self.assertEqual(
                record["sources"],
                [{"locator": None, "source": "<residual-map>"}],
            )
            self.assertIsNone(record["transformation"])

            before = (entry / "evidence.json").read_bytes()
            mismatched = run(
                entry,
                "evidence",
                "update",
                *common,
                "--id",
                "residual-map",
                "--source",
                "duplicate",
            )
            self.assertEqual(mismatched.returncode, 2)
            self.assertIn("association.artifact.source_mismatch", mismatched.stderr)
            self.assertEqual((entry / "evidence.json").read_bytes(), before)

            selected = run(entry, *arguments, "--select", "/value")
            self.assertEqual(selected.returncode, 2)
            self.assertEqual((entry / "evidence.json").read_bytes(), before)

            listed = run(entry, "evidence", "list", *common)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(
                authoring_result(listed)["records"][0]["kind"], "artifact"
            )
            unchanged = run(
                entry,
                "evidence",
                "update",
                *common,
                "--id",
                "residual-map",
                "--source",
                "residual-map",
            )
            self.assertEqual(authoring_result(unchanged)["status"], "unchanged")

            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "eid:residual-map", "eid:renamed-map"
                ),
                encoding="utf-8",
            )
            renamed = run(
                entry,
                "evidence",
                "rename",
                *common,
                "residual-map",
                "renamed-map",
            )
            self.assertEqual(renamed.returncode, 0, renamed.stderr)
            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "<!-- eid:renamed-map -->", ""
                ),
                encoding="utf-8",
            )
            removed = run(
                entry,
                "evidence",
                "remove",
                *common,
                "--id",
                "renamed-map",
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse((entry / "evidence.json").exists())

    def test_common_percentage_table_and_output_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            common = ("--path", str(logical), "--entry", "e001")
            cases = (
                (
                    "success-rate",
                    ("--source", "results", "--select", "/rate", "--as-percentage"),
                ),
                (
                    "comparison",
                    (
                        "--source",
                        "results",
                        "--select",
                        "/case",
                        "--select",
                        "/value",
                    ),
                ),
                ("run-output", ("--source", "run-log")),
            )
            for record_id, arguments in cases:
                result = run(
                    entry,
                    "evidence",
                    "add",
                    *common,
                    "--id",
                    record_id,
                    *arguments,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((entry / "evidence.json").read_text())
            self.assertEqual(
                [record["id"] for record in payload["records"]],
                ["comparison", "run-output", "success-rate"],
            )
            listed = run(entry, "evidence", "list", *common)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(len(json.loads(listed.stdout)["records"]), 3)

    def test_dry_run_and_conflict_leave_registry_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            generated = (
                logical / "validation.md",
                logical / "validation" / "results.json",
            )
            generated[0].write_text("existing report\n", encoding="utf-8")
            generated[1].parent.mkdir()
            generated[1].write_text("existing record\n", encoding="utf-8")
            generated_before = {path: path.read_bytes() for path in generated}
            arguments = (
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "success-rate",
                "--source",
                "results",
                "--select",
                "/rate",
                "--as-percentage",
            )
            dry = run(entry, *arguments, "--dry-run")
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertFalse((entry / "evidence.json").exists())
            added = run(entry, *arguments)
            self.assertEqual(added.returncode, 0, added.stderr)
            before = (entry / "evidence.json").read_bytes()
            conflict = run(
                entry,
                *arguments[:-1],
                "--scale",
                "2",
            )
            self.assertNotEqual(conflict.returncode, 0)
            self.assertEqual((entry / "evidence.json").read_bytes(), before)
            self.assertEqual(
                {path: path.read_bytes() for path in generated}, generated_before
            )

    def test_common_scalar_rendering_and_typed_direct_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            source = entry / "data" / "typed.json"
            source.write_text(
                '{"count":1000,"first":1.2,'
                '"matrix":[[1.2,true],[2.5,false]],'
                '"tiny":0.00000000000004255}\n',
                encoding="utf-8",
            )
            add_input(entry, "typed", source)
            document = entry / "e001.md"
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\nThe first value was `1.20`<!-- eid:first-value -->.\n\n"
                "The scaled count was `2,000 cases`<!-- eid:scaled-count -->.\n\n"
                "The small value was `4.26e-14`<!-- eid:scaled-tiny -->.\n\n"
                "<!-- eid:typed-table -->\n"
                "Value | Ready\n---: | ---\n"
                "1.20 | yes\n2.50 | no\n",
                encoding="utf-8",
            )
            common = ("--path", str(logical), "--entry", "e001")

            scalar = run(
                entry,
                "evidence",
                "add",
                *common,
                "--id",
                "first-value",
                "--source",
                "typed",
                "--select",
                "/first",
            )
            table = run(
                entry,
                "evidence",
                "add",
                *common,
                "--id",
                "typed-table",
                "--source",
                "typed",
                "--select",
                "/matrix",
            )
            scaled_count = run(
                entry,
                "evidence",
                "add",
                *common,
                "--id",
                "scaled-count",
                "--source",
                "typed",
                "--select",
                "/count",
                "--scale",
                "2",
            )
            scaled_tiny = run(
                entry,
                "evidence",
                "add",
                *common,
                "--id",
                "scaled-tiny",
                "--source",
                "typed",
                "--select",
                "/tiny",
                "--scale",
                "1",
            )

            self.assertEqual(scalar.returncode, 0, scalar.stderr)
            self.assertEqual(table.returncode, 0, table.stderr)
            self.assertEqual(scaled_count.returncode, 0, scaled_count.stderr)
            self.assertEqual(scaled_tiny.returncode, 0, scaled_tiny.stderr)
            records = {
                record["id"]: record
                for record in json.loads(
                    (entry / "evidence.json").read_text(encoding="utf-8")
                )["records"]
            }
            self.assertEqual(
                records["typed-table"]["transformation"]["columns"],
                [
                    {
                        "form": "scalar",
                        "value": {
                            "render": {"decimal_places": 2, "mode": "fixed"}
                        },
                    },
                    {"form": "boolean", "style": "yes_no"},
                ],
            )

    def test_add_and_update_have_exact_no_op_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            arguments = (
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "success-rate",
                "--source",
                "results",
                "--select",
                "/rate",
                "--as-percentage",
            )
            added = run(entry, "evidence", "add", *arguments)
            repeated = run(entry, "evidence", "add", *arguments)
            self.assertEqual(added.returncode, 0, added.stderr)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(authoring_result(repeated)["status"], "unchanged")
            before = (entry / "evidence.json").read_bytes()

            document = entry / "e001.md"
            document.write_text(
                document.read_text(encoding="utf-8").replace("67.6%", "67.60%"),
                encoding="utf-8",
            )
            updated = run(entry, "evidence", "update", *arguments)
            self.assertEqual(updated.returncode, 0, updated.stderr)
            self.assertNotEqual((entry / "evidence.json").read_bytes(), before)
            record = json.loads((entry / "evidence.json").read_text())["records"][0]
            self.assertEqual(record["transformation"]["decimal_places"], 2)
            unchanged = run(entry, "evidence", "update", *arguments)
            self.assertEqual(authoring_result(unchanged)["status"], "unchanged")

    def test_rename_and_remove_require_agent_first_markdown_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            common = ("--path", str(logical), "--entry", "e001")
            add = run(
                entry,
                "evidence",
                "add",
                *common,
                "--id",
                "success-rate",
                "--source",
                "results",
                "--select",
                "/rate",
                "--as-percentage",
            )
            self.assertEqual(add.returncode, 0, add.stderr)
            before = (entry / "evidence.json").read_bytes()

            early = run(
                entry,
                "evidence",
                "rename",
                *common,
                "success-rate",
                "renamed-rate",
            )
            self.assertEqual(early.returncode, 2)
            self.assertEqual((entry / "evidence.json").read_bytes(), before)

            document = entry / "e001.md"
            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "eid:success-rate", "eid:renamed-rate"
                ),
                encoding="utf-8",
            )
            renamed = run(
                entry,
                "evidence",
                "rename",
                *common,
                "success-rate",
                "renamed-rate",
            )
            self.assertEqual(renamed.returncode, 0, renamed.stderr)

            refused = run(
                entry,
                "evidence",
                "remove",
                *common,
                "--id",
                "renamed-rate",
            )
            self.assertEqual(refused.returncode, 2)
            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "<!-- eid:renamed-rate -->", ""
                ),
                encoding="utf-8",
            )
            removed = run(
                entry,
                "evidence",
                "remove",
                *common,
                "--id",
                "renamed-rate",
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse((entry / "evidence.json").exists())

    def test_failed_authoring_result_is_bounded_and_task_specific(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            result = run(
                entry,
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "missing",
                "--source",
                "results",
                "--select",
                "/rate",
            )
            self.assertEqual(result.returncode, 2)
            payload = authoring_result(result)
            self.assertEqual(payload["task"], "evidence.add")
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["code"], "evidence.presentation.unresolved")
            self.assertNotIn("records", payload)
            self.assertIn("evidence.presentation.unresolved", result.stderr)

    def test_common_mode_rejects_multiple_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            common = (
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "success-rate",
            )
            multiple = run(
                entry,
                *common,
                "--source",
                "results",
                "--source",
                "run-log",
                "--select",
                "/rate",
            )
            missing_definition = run(
                entry,
                *common,
                "--definition",
                "/private/tmp/evidence-definition.json",
            )

            self.assertEqual(multiple.returncode, 2)
            self.assertEqual(
                authoring_result(multiple)["code"], "evidence.common.unsupported"
            )
            self.assertEqual(missing_definition.returncode, 2)
            self.assertEqual(
                authoring_result(missing_definition)["code"],
                "evidence.definition.invalid",
            )
            self.assertFalse((entry / "evidence.json").exists())


class LogEvidenceDefinitionTests(unittest.TestCase):
    def test_definition_dry_run_add_no_op_conflict_and_update(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory(dir="/private/tmp") as definition_directory,
        ):
            logical, entry = fixture(Path(directory))
            generated = (
                logical / "validation.md",
                logical / "validation" / "results.json",
            )
            generated[0].write_text("existing report\n", encoding="utf-8")
            generated[1].parent.mkdir()
            generated[1].write_text("existing record\n", encoding="utf-8")
            unrelated = entry / "notes.txt"
            unrelated.write_text("unrelated\n", encoding="utf-8")
            preserved = {
                path: path.read_bytes() for path in (*generated, unrelated)
            }
            transient = Path(definition_directory)
            definition = {
                "sources": [
                    {
                        "source": "<results>",
                        "locator": {
                            "select": [["rate"]],
                            "expect": {"items": 1, "matches": 1},
                        },
                    }
                ],
                "transformation": {
                    "form": "percentage",
                    "source": {"input": 0, "item": 0},
                },
            }
            path = write_definition(transient, definition)
            path_before = path.read_bytes()
            common = (
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "success-rate",
                "--definition",
                str(path),
            )

            dry_run = run(entry, "evidence", "add", *common, "--dry-run")
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertEqual(authoring_result(dry_run)["status"], "dry-run")
            self.assertFalse((entry / "evidence.json").exists())
            self.assertEqual(path.read_bytes(), path_before)

            added = run(entry, "evidence", "add", *common)
            repeated = run(entry, "evidence", "add", *common)
            self.assertEqual(added.returncode, 0, added.stderr)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(authoring_result(repeated)["status"], "unchanged")
            record = json.loads((entry / "evidence.json").read_text())["records"][0]
            self.assertEqual(record["sources"], definition["sources"])
            self.assertEqual(record["transformation"], definition["transformation"])

            conflicting = {
                **definition,
                "sources": [
                    {
                        "source": "<results>",
                        "locator": {"select": [["rate"]]},
                    }
                ],
            }
            write_definition(transient, conflicting)
            before_conflict = (entry / "evidence.json").read_bytes()
            conflict = run(entry, "evidence", "add", *common)
            self.assertEqual(conflict.returncode, 2)
            self.assertEqual(
                authoring_result(conflict)["code"], "evidence.record.conflict"
            )
            self.assertEqual((entry / "evidence.json").read_bytes(), before_conflict)

            document = entry / "e001.md"
            document.write_text(
                document.read_text(encoding="utf-8").replace("67.6%", "67.60%"),
                encoding="utf-8",
            )
            updated_definition = {
                **definition,
                "transformation": {
                    "decimal_places": 2,
                    "form": "percentage",
                    "source": {"input": 0, "item": 0},
                },
            }
            write_definition(transient, updated_definition)
            updated_definition_bytes = path.read_bytes()
            updated = run(entry, "evidence", "update", *common)
            self.assertEqual(updated.returncode, 0, updated.stderr)
            updated_record = json.loads((entry / "evidence.json").read_text())[
                "records"
            ][0]
            self.assertEqual(updated_record["transformation"]["decimal_places"], 2)
            self.assertEqual(path.read_bytes(), updated_definition_bytes)
            self.assertEqual(
                {item: item.read_bytes() for item in preserved}, preserved
            )

    def test_definition_rejects_shape_encoding_location_and_arguments(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory(dir="/private/tmp") as definition_directory,
        ):
            logical, entry = fixture(Path(directory))
            transient = Path(definition_directory)
            common = (
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "success-rate",
            )
            invalid = (
                b'{"sources":[],"transformation":null,"id":"x"}',
                b'{"sources":[]}',
                b'{"sources":[],"sources":[],"transformation":null}',
                b'\xef\xbb\xbf{"sources":[],"transformation":null}',
                b'{"sources":[],"transformation":null} trailing',
                b'{"sources":[],"transformation":NaN}',
                b'\xff',
            )
            for index, payload in enumerate(invalid):
                with self.subTest(index=index):
                    path = write_definition(transient, payload)
                    before = path.read_bytes()
                    result = run(entry, *common, "--definition", str(path))
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(
                        authoring_result(result)["code"],
                        "evidence.definition.invalid",
                    )
                    self.assertEqual(path.read_bytes(), before)
                    self.assertFalse((entry / "evidence.json").exists())

            outside = write_definition(
                Path(directory), {"sources": [], "transformation": None}
            )
            outside_result = run(entry, *common, "--definition", str(outside))
            self.assertEqual(
                authoring_result(outside_result)["code"],
                "evidence.definition.location",
            )
            target = write_definition(
                transient, {"sources": [], "transformation": None}
            )
            link = transient / "definition-link.json"
            link.symlink_to(target)
            linked = run(entry, *common, "--definition", str(link))
            self.assertEqual(
                authoring_result(linked)["code"], "evidence.definition.unsafe"
            )
            conflicts = (
                ("--select", "/rate"),
                ("--identity", "/case"),
                ("--where", "/case", "string", "candidate"),
                ("--as-percentage",),
                ("--scale", "2"),
            )
            for arguments in conflicts:
                with self.subTest(arguments=arguments):
                    conflict = run(
                        entry,
                        *common,
                        "--definition",
                        str(target),
                        *arguments,
                    )
                    self.assertEqual(
                        authoring_result(conflict)["code"],
                        "evidence.definition.arguments_conflict",
                    )
            source_conflict = run(
                entry,
                *common,
                "--definition",
                str(target),
                "--source",
                "results",
            )
            self.assertEqual(source_conflict.returncode, 2)
            self.assertIn("not allowed with argument", source_conflict.stderr)

    def test_definition_supports_non_table_forms_and_locator_operations(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory(dir="/private/tmp") as definition_directory,
        ):
            logical, entry = fixture(Path(directory))
            source = entry / "data" / "advanced.csv"
            source.write_text(
                "case,value,uncertainty,lower,upper,label\n"
                "case-8,3.417,0.084,1.118,1.449,alpha\n"
                "case-15,4.184,0.095,1.143,1.319,beta\n",
                encoding="utf-8",
            )
            nested = entry / "data" / "nested.json"
            nested.write_text(
                '{"matrix":[["a","b"],["c","d"]],'
                '"pair":[1.118,1.449],'
                '"simulation":{"rate":0.676}}\n',
                encoding="utf-8",
            )
            add_input(entry, "advanced", source)
            add_input(entry, "nested", nested)
            document = entry / "e001.md"
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\nThe label was `alpha`<!-- eid:identity-label -->.\n\n"
                "The scalar was `3.42 ms`<!-- eid:scalar-value -->.\n\n"
                "The range was `3.42–4.18 ms`<!-- eid:range-value -->.\n\n"
                "The estimate was `3.42 ± 0.08 mas`<!-- eid:plus-minus -->.\n\n"
                "The interval was `3.42 [1.12, 1.45] ms`"
                "<!-- eid:interval-value -->.\n\n"
                "The bounds were `(1.12, 1.45)`<!-- eid:tuple-value -->.\n\n"
                "The paired rates were `(0.68, 0.68)`"
                "<!-- eid:multi-source-tuple -->.\n\n"
                "The shape count was `2`<!-- eid:shape-count -->.\n\n"
                "<!-- eid:text-output -->\n```text\ncompleted\n```\n",
                encoding="utf-8",
            )

            def numeric(item: int, *, places: int = 2) -> dict[str, object]:
                return {
                    "parse": "decimal",
                    "render": {"decimal_places": places, "mode": "fixed"},
                    "source": {"input": 0, "item": item},
                }

            def native_numeric(item: int) -> dict[str, object]:
                return {
                    "render": {"decimal_places": 2, "mode": "fixed"},
                    "source": {"input": 0, "item": item},
                }

            def one_row(*fields: str) -> dict[str, object]:
                return {
                    "select": [[field_name] for field_name in fields],
                    "where": [
                        {"op": "eq", "path": ["case"], "value": "case-8"}
                    ],
                    "expect": {"items": len(fields), "matches": 1},
                }
            definitions: dict[str, object] = {
                "identity-label": {
                    "sources": [
                        {
                            "source": "<advanced>",
                            "locator": {
                                "select": [["label"]],
                                "where": [
                                    {
                                        "op": "in",
                                        "path": ["case"],
                                        "values": ["case-8"],
                                    }
                                ],
                                "identity": [["case"]],
                                "expect": {
                                    "identities": [["case-8"]],
                                    "items": 1,
                                    "matches": 1,
                                },
                            },
                        }
                    ],
                    "transformation": None,
                },
                "scalar-value": {
                    "sources": [
                        {
                            "source": "<advanced>",
                            "locator": {
                                "select": [["value"]],
                                "where": [
                                    {
                                        "op": "eq",
                                        "path": ["case"],
                                        "value": "case-8",
                                    }
                                ],
                                "expect": {"items": 1, "matches": 1},
                            },
                        }
                    ],
                    "transformation": {
                        "form": "scalar",
                        "unit": "ms",
                        "values": [numeric(0)],
                    },
                },
                "success-rate": {
                    "sources": [
                        {
                            "source": "<nested>",
                            "locator": {
                                "path": ["simulation", "rate"],
                                "expect": {"items": 1, "matches": 1},
                            },
                        }
                    ],
                    "transformation": {
                        "form": "percentage",
                        "source": {"input": 0, "item": 0},
                    },
                },
                "range-value": {
                    "sources": [
                        {
                            "source": "<advanced>",
                            "locator": {
                                "select": [["value"]],
                                "where": [
                                    {
                                        "op": "in",
                                        "path": ["case"],
                                        "values": ["case-8", "case-15"],
                                    }
                                ],
                                "identity": [["case"]],
                                "expect": {
                                    "identities": [["case-8"], ["case-15"]],
                                    "items": 2,
                                    "matches": 2,
                                },
                            },
                        }
                    ],
                    "transformation": {
                        "form": "range",
                        "unit": "ms",
                        "values": [numeric(0), numeric(1)],
                    },
                },
                "plus-minus": {
                    "sources": [
                        {
                            "source": "<advanced>",
                            "locator": one_row("value", "uncertainty"),
                        }
                    ],
                    "transformation": {
                        "form": "plus_minus",
                        "unit": "mas",
                        "values": [numeric(0), numeric(1)],
                    },
                },
                "interval-value": {
                    "sources": [
                        {
                            "source": "<advanced>",
                            "locator": one_row("value", "lower", "upper"),
                        }
                    ],
                    "transformation": {
                        "form": "interval",
                        "unit": "ms",
                        "values": [numeric(0), numeric(1), numeric(2)],
                    },
                },
                "tuple-value": {
                    "sources": [
                        {
                            "source": "<nested>",
                            "locator": {
                                "path": ["pair", {"all": True}],
                                "expect": {"items": 2},
                            },
                        }
                    ],
                    "transformation": {
                        "form": "tuple",
                        "values": [native_numeric(0), native_numeric(1)],
                    },
                },
                "multi-source-tuple": {
                    "sources": [
                        {
                            "source": "<results>",
                            "locator": {"select": [["rate"]]},
                        },
                        {
                            "source": "<nested>",
                            "locator": {"path": ["simulation", "rate"]},
                        },
                    ],
                    "transformation": {
                        "form": "tuple",
                        "values": [
                            {
                                "parse": "decimal",
                                "render": {
                                    "decimal_places": 2,
                                    "mode": "fixed",
                                },
                                "source": {"input": 0, "item": 0},
                            },
                            {
                                "render": {
                                    "decimal_places": 2,
                                    "mode": "fixed",
                                },
                                "source": {"input": 1, "item": 0},
                            },
                        ],
                    },
                },
                "shape-count": {
                    "sources": [
                        {
                            "source": "<nested>",
                            "locator": {
                                "path": ["matrix"],
                                "property": "shape[0]",
                                "expect": {"items": 1, "matches": 1},
                            },
                        }
                    ],
                    "transformation": {
                        "form": "scalar",
                        "values": [
                            {
                                "render": {"mode": "integer"},
                                "source": {"input": 0, "item": 0},
                            }
                        ],
                    },
                },
                "text-output": {
                    "sources": [
                        {
                            "source": "<run-log>",
                            "locator": {
                                "text": {"contains": "completed", "occurrence": 1},
                                "expect": {"items": 1, "matches": 1},
                            },
                        }
                    ],
                    "transformation": {
                        "form": "text",
                        "values": [{"source": {"input": 0, "item": 0}}],
                    },
                },
            }
            common = ("--path", str(logical), "--entry", "e001")
            transient = Path(definition_directory)
            for record_id, definition in definitions.items():
                with self.subTest(record_id=record_id):
                    path = write_definition(transient, definition)
                    definition_bytes = path.read_bytes()
                    registry = entry / "evidence.json"
                    before = registry.read_bytes() if registry.exists() else None
                    dry_run = run(
                        entry,
                        "evidence",
                        "add",
                        *common,
                        "--id",
                        record_id,
                        "--definition",
                        str(path),
                        "--dry-run",
                    )
                    self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
                    self.assertEqual(
                        registry.read_bytes() if registry.exists() else None, before
                    )
                    self.assertEqual(path.read_bytes(), definition_bytes)
                    added = run(
                        entry,
                        "evidence",
                        "add",
                        *common,
                        "--id",
                        record_id,
                        "--definition",
                        str(path),
                    )
                    self.assertEqual(added.returncode, 0, added.stderr)
                    records = {
                        record["id"]: record
                        for record in json.loads(registry.read_text())["records"]
                    }
                    self.assertEqual(
                        records[record_id]["sources"], definition["sources"]
                    )
                    self.assertEqual(
                        records[record_id]["transformation"],
                        definition["transformation"],
                    )

    def test_definition_supports_all_table_modes_and_multiple_sources(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory(dir="/private/tmp") as definition_directory,
        ):
            logical, entry = fixture(Path(directory))
            source = entry / "data" / "advanced.csv"
            source.write_text(
                "case,lower,upper,flag\n"
                "case-8,1.118,1.449,True\n"
                "case-15,1.143,1.319,False\n",
                encoding="utf-8",
            )
            matrix = entry / "data" / "matrix.json"
            matrix.write_text('[["a","b"],["c","d"]]\n', encoding="utf-8")
            add_input(entry, "advanced", source)
            add_input(entry, "matrix", matrix)
            document = entry / "e001.md"
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n<!-- eid:direct-table -->\n"
                "Case | Value\n--- | ---\na | b\nc | d\n\n"
                "<!-- eid:structured-table -->\n"
                "Case | Error range\n--- | ---\n"
                "case-15 | 1.14–1.32%\ncase-8 | 1.12–1.45%\n\n"
                "<!-- eid:summary-table -->\n"
                "Metric | Status | Bounds\n--- | --- | ---\n"
                "Detector | Pass | 1.1 / 1.4%\n",
                encoding="utf-8",
            )

            def field(field: int) -> dict[str, object]:
                return {
                    "parse": "decimal",
                    "render": {"decimal_places": 2, "mode": "fixed"},
                    "source": {"field": field, "input": 0},
                }

            definitions = {
                "direct-table": {
                    "sources": [
                        {
                            "source": "<matrix>",
                            "locator": {
                                "path": [],
                                "expect": {
                                    "items": 1,
                                    "matches": 1,
                                    "shape": [2],
                                },
                            },
                        }
                    ],
                    "transformation": {
                        "columns": [{"form": "text"}, {"form": "text"}],
                        "form": "table",
                        "headings": ["Case", "Value"],
                        "mode": "direct",
                    },
                },
                "structured-table": {
                    "sources": [
                        {
                            "source": "<advanced>",
                            "locator": {
                                "select": [["case"], ["lower"], ["upper"]],
                                "identity": [["case"]],
                                "expect": {
                                    "identities": [["case-8"], ["case-15"]],
                                    "items": 6,
                                    "matches": 2,
                                },
                            },
                        }
                    ],
                    "transformation": {
                        "columns": [
                            {
                                "form": "text",
                                "values": [
                                    {"source": {"field": 0, "input": 0}}
                                ],
                            },
                            {
                                "form": "range",
                                "unit": "%",
                                "values": [field(1), field(2)],
                            },
                        ],
                        "form": "table",
                        "headings": ["Case", "Error range"],
                        "mode": "structured",
                        "rows": {
                            "input": 0,
                            "order": [["case-15"], ["case-8"]],
                        },
                    },
                },
                "summary-table": {
                    "sources": [
                        {
                            "source": "<advanced>",
                            "locator": {
                                "select": [["flag"]],
                                "where": [
                                    {
                                        "op": "eq",
                                        "path": ["case"],
                                        "value": "case-8",
                                    }
                                ],
                            },
                        },
                        {
                            "source": "<advanced>",
                            "locator": {
                                "select": [["lower"], ["upper"]],
                                "where": [
                                    {
                                        "op": "eq",
                                        "path": ["case"],
                                        "value": "case-8",
                                    }
                                ],
                            },
                        },
                    ],
                    "transformation": {
                        "form": "table",
                        "headings": ["Metric", "Status", "Bounds"],
                        "mode": "summary",
                        "rows": [
                            [
                                {"form": "label", "text": "Detector"},
                                {
                                    "form": "boolean",
                                    "style": "pass_fail",
                                    "values": [
                                        {
                                            "parse": "boolean",
                                            "source": {"input": 0, "item": 0},
                                        }
                                    ],
                                },
                                {
                                    "form": "sequence",
                                    "style": "slash",
                                    "unit": "%",
                                    "values": [
                                        {
                                            "parse": "decimal",
                                            "render": {
                                                "decimal_places": 1,
                                                "mode": "fixed",
                                            },
                                            "source": {"input": 1, "item": item},
                                        }
                                        for item in range(2)
                                    ],
                                },
                            ]
                        ],
                    },
                },
            }
            common = ("--path", str(logical), "--entry", "e001")
            transient = Path(definition_directory)
            for record_id, definition in definitions.items():
                with self.subTest(record_id=record_id):
                    path = write_definition(transient, definition)
                    before = (
                        (entry / "evidence.json").read_bytes()
                        if (entry / "evidence.json").exists()
                        else None
                    )
                    dry_run = run(
                        entry,
                        "evidence",
                        "add",
                        *common,
                        "--id",
                        record_id,
                        "--definition",
                        str(path),
                        "--dry-run",
                    )
                    self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
                    self.assertEqual(
                        (entry / "evidence.json").read_bytes()
                        if (entry / "evidence.json").exists()
                        else None,
                        before,
                    )
                    added = run(
                        entry,
                        "evidence",
                        "add",
                        *common,
                        "--id",
                        record_id,
                        "--definition",
                        str(path),
                    )
                    self.assertEqual(added.returncode, 0, added.stderr)

    def test_definition_rejects_semantic_failures_without_changes(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory(dir="/private/tmp") as definition_directory,
        ):
            logical, entry = fixture(Path(directory))
            transient = Path(definition_directory)
            common = (
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "success-rate",
            )
            failures = (
                {"sources": [], "transformation": None},
                {
                    "sources": [
                        {"source": "<missing>", "locator": {"path": []}}
                    ],
                    "transformation": None,
                },
                {
                    "sources": [
                        {
                            "source": "<results>",
                            "locator": {"unknown": [["rate"]]},
                        }
                    ],
                    "transformation": None,
                },
                {
                    "sources": [
                        {
                            "source": "<results>",
                            "locator": {"select": [["rate"]]},
                        }
                    ],
                    "transformation": {"form": "unknown"},
                },
                {
                    "sources": [
                        {
                            "source": "<results>",
                            "locator": {"select": [["case"], ["rate"]]},
                        }
                    ],
                    "transformation": {
                        "form": "percentage",
                        "source": {"input": 0, "item": 0},
                    },
                },
                {
                    "sources": [
                        {
                            "source": "<results>",
                            "locator": {"select": [["rate"]]},
                        }
                    ],
                    "transformation": {
                        "form": "scalar",
                        "values": [
                            {
                                "parse": "decimal",
                                "render": {
                                    "decimal_places": 2,
                                    "mode": "fixed",
                                },
                                "source": {"input": 0, "item": 0},
                            }
                        ],
                    },
                },
            )
            for index, definition in enumerate(failures):
                with self.subTest(index=index):
                    path = write_definition(transient, definition)
                    result = run(entry, *common, "--definition", str(path))
                    self.assertEqual(result.returncode, 2)
                    self.assertFalse((entry / "evidence.json").exists())

            source = entry / "data" / "results.csv"
            source.write_text(
                source.read_text(encoding="utf-8") + "other,0.1,value\n",
                encoding="utf-8",
            )
            valid = {
                "sources": [
                    {
                        "source": "<results>",
                        "locator": {"select": [["rate"]]},
                    }
                ],
                "transformation": {
                    "form": "percentage",
                    "source": {"input": 0, "item": 0},
                },
            }
            path = write_definition(transient, valid)
            stale = run(entry, *common, "--definition", str(path))
            self.assertEqual(stale.returncode, 2)
            self.assertIn("data.fingerprint.mismatch", stale.stderr)
            self.assertFalse((entry / "evidence.json").exists())

    def test_definition_rejects_marker_ambiguity_and_oversized_input(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory(dir="/private/tmp") as definition_directory,
        ):
            logical, entry = fixture(Path(directory))
            transient = Path(definition_directory)
            definition = {
                "sources": [
                    {
                        "source": "<results>",
                        "locator": {"select": [["rate"]]},
                    }
                ],
                "transformation": {
                    "form": "percentage",
                    "source": {"input": 0, "item": 0},
                },
            }
            common = (
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "success-rate",
                "--definition",
            )
            duplicate = entry / "duplicate.md"
            duplicate.write_text(
                "The duplicate was `67.6%`<!-- eid:success-rate -->.\n",
                encoding="utf-8",
            )
            path = write_definition(transient, definition)
            ambiguous = run(entry, *common, str(path))
            self.assertEqual(ambiguous.returncode, 2)
            self.assertIn("evidence.presentation.unresolved", ambiguous.stderr)
            self.assertFalse((entry / "evidence.json").exists())

            duplicate.unlink()
            path.write_bytes(b" " * (8 * 1024 * 1024 + 1))
            oversized = run(entry, *common, str(path))
            self.assertEqual(oversized.returncode, 2)
            self.assertEqual(
                authoring_result(oversized)["code"], "evidence.definition.invalid"
            )
            self.assertFalse((entry / "evidence.json").exists())

    def test_definition_publication_failure_preserves_existing_state(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory(dir="/private/tmp") as definition_directory,
        ):
            logical, entry = fixture(Path(directory))
            definition = write_definition(
                Path(definition_directory),
                {
                    "sources": [
                        {
                            "source": "<results>",
                            "locator": {"select": [["rate"]]},
                        }
                    ],
                    "transformation": {
                        "form": "percentage",
                        "source": {"input": 0, "item": 0},
                    },
                },
            )
            added = run(
                entry,
                "evidence",
                "add",
                "--path",
                str(logical),
                "--entry",
                "e001",
                "--id",
                "success-rate",
                "--definition",
                str(definition),
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            before = (entry / "evidence.json").read_bytes()
            document = entry / "e001.md"
            document.write_text(
                document.read_text(encoding="utf-8").replace("67.6%", "67.60%"),
                encoding="utf-8",
            )
            write_definition(
                Path(definition_directory),
                {
                    "sources": [
                        {
                            "source": "<results>",
                            "locator": {"select": [["rate"]]},
                        }
                    ],
                    "transformation": {
                        "decimal_places": 2,
                        "form": "percentage",
                        "source": {"input": 0, "item": 0},
                    },
                },
            )
            script_root = str(LOG.parent)
            sys.path.insert(0, script_root)
            try:
                from log_commands import evidence, evidence_definition
                from log_commands.context import resolve_entry, resolve_log

                context = resolve_entry(resolve_log(logical), "e001")
                with (
                    mock.patch.object(
                        evidence,
                        "remove_or_write",
                        side_effect=OSError("injected publication failure"),
                    ),
                    self.assertRaisesRegex(OSError, "injected publication failure"),
                ):
                    evidence_definition.add_or_update(
                        context,
                        action="update",
                        record_id="success-rate",
                        definition=definition,
                        dry_run=False,
                    )
            finally:
                sys.path.remove(script_root)
            self.assertEqual((entry / "evidence.json").read_bytes(), before)


class LogRetentionTests(unittest.TestCase):
    def test_add_update_rename_list_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            common = ("--path", str(logical), "--entry", "e001")
            added = run(
                entry,
                "retention",
                "add",
                *common,
                "--id",
                "keep-run",
                "--reason",
                "diagnostic",
                "data/run.log",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            updated = run(
                entry,
                "retention",
                "update",
                *common,
                "--id",
                "keep-run",
                "data/results.csv",
            )
            self.assertEqual(updated.returncode, 0, updated.stderr)
            renamed = run(
                entry,
                "retention",
                "rename",
                *common,
                "keep-run",
                "keep-results",
            )
            self.assertEqual(renamed.returncode, 0, renamed.stderr)
            listed = json.loads(run(entry, "retention", "list", *common).stdout)
            self.assertEqual(listed["records"][0]["id"], "keep-results")
            removed = run(
                entry,
                "retention",
                "remove",
                *common,
                "--id",
                "keep-results",
            )
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse((entry / "retention.json").exists())

    def test_directory_and_overlap_validation_are_transactional(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            retained = entry / "data" / "retained"
            retained.mkdir()
            (retained / "a.txt").write_text("a", encoding="utf-8")
            common = ("--path", str(logical), "--entry", "e001")
            added = run(
                entry,
                "retention",
                "add",
                *common,
                "--id",
                "directory",
                "data/retained",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            before = (entry / "retention.json").read_bytes()
            overlap = run(
                entry,
                "retention",
                "add",
                *common,
                "--id",
                "member",
                "data/retained/a.txt",
            )
            self.assertEqual(overlap.returncode, 2)
            self.assertEqual((entry / "retention.json").read_bytes(), before)
            mixed = run(
                entry,
                "retention",
                "add",
                *common,
                "--id",
                "mixed",
                "data/retained",
                "data/run.log",
            )
            self.assertEqual(mixed.returncode, 2)
            self.assertEqual((entry / "retention.json").read_bytes(), before)

    def test_absent_remove_and_absolute_target_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = fixture(Path(directory))
            common = ("--path", str(logical), "--entry", "e001")
            absent = run(
                entry,
                "retention",
                "remove",
                *common,
                "--id",
                "not-present",
            )
            absolute = run(
                entry,
                "retention",
                "add",
                *common,
                "--id",
                "absolute",
                str(entry / "data/run.log"),
            )

            self.assertEqual(absent.returncode, 0, absent.stderr)
            self.assertEqual(authoring_result(absent)["status"], "absent")
            self.assertEqual(absolute.returncode, 2)
            self.assertEqual(
                authoring_result(absolute)["code"], "retention.target.invalid"
            )
            self.assertFalse((entry / "retention.json").exists())


class PyrunQuarantineTests(unittest.TestCase):
    def test_malformed_output_support_is_preserved_and_execution_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry = fixture(Path(directory))
            script = entry / "scripts" / "run.py"
            script.write_text(
                "from pathlib import Path\nPath('data/executed').write_text('yes')\n",
                encoding="utf-8",
            )
            malformed = b'{"broken":'
            (entry / "pyrun-outputs.json").write_bytes(malformed)

            result = subprocess.run(
                [sys.executable, str(PYRUN), "scripts/run.py"],
                cwd=entry,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pyrun.outputs.quarantined", result.stderr)
            self.assertEqual((entry / "pyrun-outputs.json.bak").read_bytes(), malformed)
            self.assertEqual(
                json.loads((entry / "pyrun-outputs.json").read_text())["outputs"], {}
            )
            self.assertFalse((entry / "data" / "executed").exists())

    def test_quarantine_uses_first_unused_numbered_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, entry = fixture(Path(directory))
            script = entry / "scripts" / "run.py"
            script.write_text("raise SystemExit('must not run')\n", encoding="utf-8")
            malformed = b"not-json\n"
            (entry / "pyrun-outputs.json").write_bytes(malformed)
            (entry / "pyrun-outputs.json.bak").write_bytes(b"first")
            (entry / "pyrun-outputs.json.2.bak").write_bytes(b"second")

            result = subprocess.run(
                [sys.executable, str(PYRUN), "scripts/run.py"],
                cwd=entry,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(
                (entry / "pyrun-outputs.json.3.bak").read_bytes(), malformed
            )
            self.assertEqual((entry / "pyrun-outputs.json.bak").read_bytes(), b"first")
            self.assertEqual(
                (entry / "pyrun-outputs.json.2.bak").read_bytes(), b"second"
            )

    def test_unsafe_output_support_path_is_not_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, entry = fixture(root)
            script = entry / "scripts" / "run.py"
            script.write_text(
                "from pathlib import Path\n"
                "Path('data/executed').write_text('yes')\n",
                encoding="utf-8",
            )
            external = root / "external.json"
            external.write_bytes(b"not-json")
            current = entry / "pyrun-outputs.json"
            current.symlink_to(external)

            result = subprocess.run(
                [sys.executable, str(PYRUN), "scripts/run.py"],
                cwd=entry,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("pyrun.outputs.invalid", result.stderr)
            self.assertTrue(current.is_symlink())
            self.assertFalse((entry / "pyrun-outputs.json.bak").exists())
            self.assertFalse((entry / "data/executed").exists())


if __name__ == "__main__":
    unittest.main()
