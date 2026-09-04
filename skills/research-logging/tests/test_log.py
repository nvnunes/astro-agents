from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LOG = Path(__file__).resolve().parents[1] / "scripts" / "log"
PYRUN = Path(__file__).resolve().parents[1] / "scripts" / "pyrun"
VALIDATION = (
    Path(__file__).resolve().parents[1] / "scripts" / "research_log_validation.py"
)


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
        "- [Study](study/entries/2026-09-03-e001-study/e001.md)\n",
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
        self.assertIn("evidence", top.stdout)
        self.assertNotIn("--source", top.stdout)

        family = run(Path.cwd(), "evidence", "--help")
        self.assertEqual(family.returncode, 0, family.stderr)
        self.assertIn("add", family.stdout)
        self.assertNotIn("--source", family.stdout)

        action = run(Path.cwd(), "evidence", "add", "--help")
        self.assertEqual(action.returncode, 0, action.stderr)
        self.assertIn("--source", action.stdout)

    def test_help_and_selected_family_imports_are_lazy(self) -> None:
        script_root = LOG.parent
        code = f"""
import json
import sys
sys.path.insert(0, {str(script_root)!r})
from log_commands.dispatcher import main
for arguments in ([\"evidence\", \"--help\"], [\"evidence\", \"add\", \"--help\"]):
    try:
        main(arguments)
    except SystemExit as error:
        assert error.code == 0
print(json.dumps({{
    \"evidence\": \"log_commands.evidence\" in sys.modules,
    \"retention\": \"log_commands.retention\" in sys.modules,
    \"validation\": \"validation.controller\" in sys.modules,
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
                "validation": False,
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

            result = run(
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
    def test_new_routes_preserve_discovery_and_one_log_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical, _ = fixture(root)
            environment = os.environ.copy()
            environment.pop("PYTHONHOME", None)
            common = ("--date", "2026-09-03", "--dry-run", "--recompute")
            legacy = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATION),
                    "validate",
                    "--summary",
                    str(logical.with_suffix(".md")),
                    *common,
                ],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            current = run(root, "validate", "--path", str(logical), *common)
            self.assertEqual(current.returncode, legacy.returncode, current.stderr)
            current_payload = json.loads(current.stdout)
            legacy_payload = json.loads(legacy.stdout)
            self.assertEqual(
                set(current_payload["metrics"]), set(legacy_payload["metrics"])
            )
            current_payload.pop("metrics")
            legacy_payload.pop("metrics")
            self.assertEqual(current_payload, legacy_payload)

            old_discovery = subprocess.run(
                [
                    sys.executable,
                    str(VALIDATION),
                    "discover",
                    "--root",
                    str(root),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            new_discovery = run(root, "discover", "--root", str(root))
            self.assertEqual(new_discovery.returncode, old_discovery.returncode)
            self.assertEqual(
                json.loads(new_discovery.stdout), json.loads(old_discovery.stdout)
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
            same = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    acquire_code,
                    str(logical),
                    "entry-e001.lock",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    same.communicate(timeout=0.2)
            finally:
                self.assertIsNotNone(holder.stdin)
                holder.stdin.write("\n")
                holder.stdin.flush()
                holder.communicate(timeout=2)
            stdout, stderr = same.communicate(timeout=2)
            self.assertEqual(stdout.strip(), "acquired", stderr)


class LogEvidenceTests(unittest.TestCase):
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
                logical / "validation" / "mechanical.json",
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

    def test_common_mode_rejects_multiple_sources_and_reserved_definition(self) -> None:
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
            reserved = run(
                entry,
                *common,
                "--definition",
                "/private/tmp/evidence-definition.json",
            )

            self.assertEqual(multiple.returncode, 2)
            self.assertEqual(
                authoring_result(multiple)["code"], "evidence.common.unsupported"
            )
            self.assertEqual(reserved.returncode, 2)
            self.assertEqual(
                authoring_result(reserved)["code"],
                "evidence.definition.unavailable",
            )
            self.assertFalse((entry / "evidence.json").exists())


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
