from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from research_log_cli_test_support import run_log, run_pyrun_process
from validation.pyrun_state import load_pyrun_state


def payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout)


class ResearchLogIntegratedWorkflowTests(unittest.TestCase):
    def test_current_authoring_execution_and_validation_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            log = project / "docs" / "study"
            log.parent.mkdir()

            initialized = run_log(
                project,
                "init",
                "--path",
                str(log),
                "--title",
                "Integrated study",
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            added = run_log(
                project,
                "add",
                "--path",
                str(log),
                "--date",
                "2030-01-02",
                "--title",
                "Deterministic run",
                "--slug",
                "deterministic-run",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            entry = next((log / "entries").iterdir())
            (entry / "data").mkdir()
            (entry / "scripts").mkdir()
            source = entry / "data" / "source.csv"
            source.write_text("value\n1\n", encoding="utf-8")
            script = entry / "scripts" / "build.py"
            script.write_text(
                "import argparse, shutil\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--input', required=True)\n"
                "p.add_argument('--output', required=True)\n"
                "p.add_argument('--label')\n"
                "a = p.parse_args()\n"
                "shutil.copyfile(a.input, a.output)\n",
                encoding="utf-8",
            )
            document = entry / "e001.md"
            command = (
                './pyrun --cid build -- scripts/build.py --input "<source>" '
                '--output "<result>"'
            )
            document.write_text(
                "# 2030-01-02: Deterministic run\n\n"
                "## Build\n\n"
                "`Background:`\n\nExercise the complete current-format path.\n\n"
                "`Steps:`\n\n```bash\n"
                f"{command}\n"
                "```\n\n`Results:`\n\nPending.\n",
                encoding="utf-8",
            )
            common = ("--path", str(log), "--entry", "e001")
            synchronized = run_log(
                entry,
                "command",
                "sync",
                *common,
                "--cid",
                "build",
                "--add-generated",
                "result=data/result.csv",
                "--add-origin",
                "source=data/source.csv",
            )
            self.assertEqual(synchronized.returncode, 0, synchronized.stderr)
            initial_state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=project
            )
            initial = next(iter(initial_state.commands["build"].executions.values()))
            self.assertTrue(initial.requires_reproduction)
            self.assertIsNone(initial.observed.script)

            executed = run_pyrun_process(
                entry,
                "--cid",
                "build",
                "--",
                "scripts/build.py",
                "--input",
                "<source>",
                "--output",
                "<result>",
            )
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertEqual(
                (entry / "data/result.csv").read_bytes(), source.read_bytes()
            )
            current_state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=project
            )
            current = next(iter(current_state.commands["build"].executions.values()))
            self.assertFalse(current.requires_reproduction)
            self.assertIsNotNone(current.observed.script)
            self.assertEqual(
                {name for name, _value in current.observed.inputs}, {"source"}
            )
            self.assertEqual(
                {name for name, _value in current.observed.outputs},
                {"data/result.csv"},
            )

            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "Pending.",
                    "[Generated result](data/result.csv)"
                    "<!-- eid:generated-result source=result -->",
                ),
                encoding="utf-8",
            )
            before_compare = document.read_bytes()
            compared = run_log(
                entry,
                "evidence",
                "compare",
                *common,
                "--producer",
                "build",
            )
            self.assertEqual(compared.returncode, 0, compared.stderr)
            self.assertEqual(
                [record["id"] for record in payload(compared)["records"]],
                ["generated-result"],
            )
            self.assertEqual(document.read_bytes(), before_compare)
            evidence = run_log(
                entry, "evidence", "sync", *common, "--producer", "build"
            )
            self.assertEqual(evidence.returncode, 0, evidence.stderr)
            evidence_before = (entry / "evidence.json").read_bytes()
            document_before = document.read_bytes()
            result_before = (entry / "data/result.csv").read_bytes()

            discovered = run_log(project, "discover", "--root", str(project))
            self.assertEqual(discovered.returncode, 0, discovered.stderr)
            self.assertEqual(
                payload(discovered)["summaries"],
                [log.with_suffix(".md").resolve().as_posix()],
            )
            entry_validation = run_log(
                project,
                "validate",
                "run",
                "--path",
                str(log),
                "--entry",
                "e001",
                "--format",
                "json",
            )
            self.assertEqual(entry_validation.returncode, 0, entry_validation.stderr)
            entry_result = payload(entry_validation)
            entry_findings = run_log(
                project,
                "validate",
                "list",
                "findings",
                "--path",
                str(log),
                "--entry",
                "e001",
                "--format",
                "json",
            )
            self.assertEqual(
                entry_result["outcome"],
                "clear",
                entry_findings.stdout,
            )
            self.assertTrue(entry_result["saved"])
            self.assertEqual(payload(entry_findings)["total"], 0)
            full_validation = run_log(
                project,
                "validate",
                "run",
                "--path",
                str(log),
                "--format",
                "json",
            )
            self.assertEqual(full_validation.returncode, 0, full_validation.stderr)
            self.assertEqual(payload(full_validation)["outcome"], "clear")
            self.assertTrue((log / ".cache/results.sqlite").is_file())
            self.assertTrue((log / "validation.md").is_file())
            self.assertEqual(document.read_bytes(), document_before)
            self.assertEqual((entry / "evidence.json").read_bytes(), evidence_before)
            self.assertEqual((entry / "data/result.csv").read_bytes(), result_before)

            original_identity = next(iter(current_state.commands["build"].executions))
            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "--cid build --",
                    "--cid build --auto-reproduce=false --",
                ),
                encoding="utf-8",
            )
            policy_sync = run_log(entry, "command", "sync", *common, "--cid", "build")
            self.assertEqual(policy_sync.returncode, 0, policy_sync.stderr)
            policy_state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=project
            )
            self.assertEqual(
                set(policy_state.commands["build"].executions), {original_identity}
            )
            policy_execution = policy_state.commands["build"].executions[
                original_identity
            ]
            self.assertFalse(policy_execution.auto_reproduce)
            self.assertFalse(policy_execution.requires_reproduction)
            self.assertEqual(policy_execution.observed, current.observed)

            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    '--output "<result>"', '--output "<result>" --label stable'
                ),
                encoding="utf-8",
            )
            retirement_required = run_log(
                entry, "command", "sync", *common, "--cid", "build"
            )
            self.assertEqual(retirement_required.returncode, 2)
            self.assertIn(
                "command.sync.execution.deletion_required", retirement_required.stderr
            )
            recipe_sync = run_log(
                entry,
                "command",
                "sync",
                *common,
                "--cid",
                "build",
                "--delete-stale-executions",
                "build",
            )
            self.assertEqual(recipe_sync.returncode, 0, recipe_sync.stderr)
            recipe_state = load_pyrun_state(
                entry / "pyrun.json", entry_root=entry, project_root=project
            )
            self.assertEqual(len(recipe_state.commands["build"].executions), 1)
            recipe_identity, recipe_execution = next(
                iter(recipe_state.commands["build"].executions.items())
            )
            self.assertNotEqual(recipe_identity, original_identity)
            self.assertTrue(recipe_execution.requires_reproduction)
            self.assertIsNone(recipe_execution.observed.script)
            recipe_run = run_pyrun_process(
                entry,
                "--cid",
                "build",
                "--auto-reproduce=false",
                "--",
                "scripts/build.py",
                "--input",
                "<source>",
                "--output",
                "<result>",
                "--label",
                "stable",
            )
            self.assertEqual(recipe_run.returncode, 0, recipe_run.stderr)

            downstream_script = entry / "scripts" / "summarize.py"
            downstream_script.write_text(script.read_text(encoding="utf-8"))
            downstream_command = (
                './pyrun --cid summarize -- scripts/summarize.py --input "<result>" '
                '--output "<final>"'
            )
            document.write_text(
                document.read_text(encoding="utf-8")
                + "\n## Summarize\n\n"
                + "`Background:`\n\nRetain a downstream result.\n\n"
                + "`Steps:`\n\n```bash\n"
                + downstream_command
                + "\n```\n\n`Results:`\n\nPending downstream result.\n",
                encoding="utf-8",
            )
            downstream_sync = run_log(
                entry,
                "command",
                "sync",
                *common,
                "--cid",
                "summarize",
                "--add-generated",
                "final=data/final.csv",
            )
            self.assertEqual(downstream_sync.returncode, 0, downstream_sync.stderr)
            downstream_run = run_pyrun_process(
                entry,
                "--cid",
                "summarize",
                "--",
                "scripts/summarize.py",
                "--input",
                "<result>",
                "--output",
                "<final>",
            )
            self.assertEqual(downstream_run.returncode, 0, downstream_run.stderr)
            document.write_text(
                document.read_text(encoding="utf-8").replace(
                    "Pending downstream result.",
                    "[Final result](data/final.csv)"
                    "<!-- eid:final-result source=final -->",
                ),
                encoding="utf-8",
            )
            final_evidence = run_log(
                entry,
                "evidence",
                "sync",
                *common,
                "--id",
                "final-result",
            )
            self.assertEqual(final_evidence.returncode, 0, final_evidence.stderr)
            second_clear = run_log(
                project,
                "validate",
                "run",
                "--path",
                str(log),
                "--format",
                "json",
            )
            self.assertEqual(second_clear.returncode, 0, second_clear.stderr)
            self.assertEqual(payload(second_clear)["outcome"], "clear")

            source_v2 = entry / "data" / "source-v2.csv"
            source_v2.write_text("value\n2\n", encoding="utf-8")
            immutable_before_change = {
                "document": document.read_bytes(),
                "evidence": (entry / "evidence.json").read_bytes(),
                "result": (entry / "data/result.csv").read_bytes(),
                "final": (entry / "data/final.csv").read_bytes(),
                "pyrun": (entry / "pyrun.json").read_bytes(),
            }
            declaration_change = run_log(
                entry,
                "data",
                "update",
                *common,
                "source",
                "--target",
                "data/source-v2.csv",
            )
            self.assertEqual(
                declaration_change.returncode, 0, declaration_change.stderr
            )
            self.assertEqual(
                (entry / "pyrun.json").read_bytes(),
                immutable_before_change["pyrun"],
            )
            fresh_downstream = run_pyrun_process(
                entry,
                "--cid",
                "summarize",
                "--",
                "scripts/summarize.py",
                "--input",
                "<result>",
                "--output",
                "<final>",
            )
            self.assertEqual(fresh_downstream.returncode, 0, fresh_downstream.stderr)
            stale_validation = run_log(
                project,
                "validate",
                "run",
                "--path",
                str(log),
                "--format",
                "json",
            )
            self.assertEqual(stale_validation.returncode, 0, stale_validation.stderr)
            stale_result = payload(stale_validation)
            self.assertEqual(stale_result["outcome"], "findings")
            self.assertEqual(
                stale_result["finding_counts_by_type"],
                {
                    "conformance": 0,
                    "evidence": 0,
                    "orphan": 1,
                    "provenance": 0,
                },
            )
            self.assertEqual(stale_result["blocked_check_count"], 0)
            blocked = run_log(
                project,
                "validate",
                "list",
                "blocked",
                "--path",
                str(log),
                "--format",
                "json",
            )
            self.assertEqual(
                blocked.returncode,
                0,
                blocked.stderr,
            )
            self.assertEqual(payload(blocked)["total"], 0)
            self.assertEqual(document.read_bytes(), immutable_before_change["document"])
            self.assertEqual(
                (entry / "evidence.json").read_bytes(),
                immutable_before_change["evidence"],
            )
            self.assertEqual(
                (entry / "data/result.csv").read_bytes(),
                immutable_before_change["result"],
            )
            self.assertEqual(
                (entry / "data/final.csv").read_bytes(),
                immutable_before_change["final"],
            )

            plan = run_log(
                project,
                "reproduce",
                "plan",
                "--path",
                str(log),
                "--include-all",
                "--format",
                "json",
            )
            self.assertEqual(plan.returncode, 0, plan.stderr)
            plan_payload = payload(plan)
            self.assertEqual(plan_payload["commands"]["ready_to_run"], 0)
            self.assertEqual(plan_payload["commands"]["blocked"], 2)
            self.assertEqual(
                {item["identity"]["cid"] for item in plan_payload["items"]},
                {"build", "summarize"},
            )
            self.assertEqual(
                {item["reason"] for item in plan_payload["items"] if item["reason"]},
                {"direct_input_changed"},
            )
            batch = run_log(
                project,
                "validate",
                "run",
                "--root",
                str(project),
                "--dry-run",
                "--format",
                "json",
            )
            self.assertEqual(batch.returncode, 0, batch.stderr)
            self.assertEqual(len(payload(batch)["rows"]), 1)
            rendered = run_log(
                project,
                "validate",
                "render",
                "--path",
                str(log),
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertIn("Validation", (log / "validation.md").read_text())


if __name__ == "__main__":
    unittest.main()
