"""Rejected producers remain invalid while their discovery blockers are inspectable."""

from __future__ import annotations

import io
import json
import shlex
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from log_commands.dispatcher import _report_failure
from log_commands.inspection_queries import Query, inspect_result
from log_commands.model import ActionError
from research_log_cli_test_support import run_log
from research_log_data import build_local_input
from research_log_validation_test_support import mechanical_log
from test_log_data import scaffold
from test_validation_inspection import sample
from validation.command_diagnostics import (
    RejectedProducerIndex,
    rejected_producer_message,
)
from validation.inspection import save_result
from validation.inspection_store import InspectionError


class RejectedProducerTests(unittest.TestCase):
    def test_large_error_is_text_only_with_read_only_paged_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = sample(Path(directory))
            logical = arguments[0].with_suffix("")
            batch_id = save_result(*arguments)
            commands = tuple({
                "identity": f"entry:e001:command:1:{i}", "entry": "e001",
                "document": "entries/e001/e001.md", "fence": 1, "ordinal": i,
                "script": "scripts/build.py", "status": "rejected",
                "code": "material.candidate.unresolved",
                "arguments": [
                    {"selector": f"catalog-{j}", "value": "data/" + "x" * 180}
                    for j in range(20)
                ],
                "declared_outputs": [{"path": "/data/shared", "kind": "directory"}],
            } for i in range(1, 21))
            error = ActionError(
                "producer.missing", rejected_producer_message(commands),
                records=commands, diagnostic_log=logical,
            )
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = _report_failure("data", "data.add-generated", error)
            self.assertEqual(status, 2)
            self.assertLess(
                len((stdout.getvalue() + stderr.getvalue()).encode()), 12000
            )
            self.assertNotIn('"arguments":', stdout.getvalue())
            self.assertNotIn("structured error records", stderr.getvalue())
            followup = next(line.removeprefix("Inspect: ")
                            for line in stdout.getvalue().splitlines()
                            if line.startswith("Inspect: "))
            result_id = inspect_result(
                logical, Query(action="list", kind="diagnostic")
            )["items"][0]["result_id"]
            with mock.patch("log_commands.materials.inspect_log_materials",
                            side_effect=AssertionError("must not reevaluate")):
                shown = run_log(logical.parent, *shlex.split(followup)[1:])
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertLessEqual(len(shown.stdout.encode()), 16384)
            self.assertIn("next_command:", shown.stdout)
            self.assertIn("log results collection", shown.stdout)
            next_command = next(line.removeprefix("next_command: ")
                                for line in shown.stdout.splitlines()
                                if line.startswith("next_command: "))
            second = run_log(logical.parent, *shlex.split(next_command)[1:])
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertNotEqual(second.stdout, shown.stdout)
            explicit = run_log(
                logical.parent, "results", "command", "--path", str(logical),
                "--id", result_id, "--command", commands[0]["identity"],
                "--format", "json",
            )
            self.assertEqual(explicit.returncode, 0, explicit.stderr)
            self.assertEqual(
                json.loads(explicit.stdout)["items"][0]["status"], "rejected"
            )
            summary = inspect_result(logical, Query(result_id=result_id))["metadata"]
            self.assertIsNone(summary["evaluated_checks"])
            self.assertEqual(summary["kind"], "diagnostic")
            # One latest diagnostic; full/batch observations remain available.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                _report_failure("data", "data.add-generated", error)
            self.assertEqual(len(inspect_result(
                logical, Query(action="list", kind="diagnostic")
            )["items"]), 1)
            with self.assertRaises(InspectionError):
                inspect_result(logical, Query(result_id=result_id))
            self.assertEqual(inspect_result(logical, Query(result_id=batch_id))[
                "metadata"
            ]["kind"], "batch")

    def test_failed_cache_write_never_falls_back_to_json(self):
        error = ActionError("producer.missing", "No admitted producer", records=(),
                            diagnostic_log=Path("/unused/log"))
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch(
            "validation.inspection.save_result", side_effect=OSError("busy")
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = _report_failure("data", "data.add-generated", error)
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("producer.missing", stderr.getvalue())
        self.assertIn("result not cached", stderr.getvalue())

    def test_unrelated_scalar_observations_still_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = sample(Path(directory))
            arguments[1]["findings"][0]["observed"] = "unavailable"
            result_id = save_result(*arguments)
            view = inspect_result(
                arguments[0].with_suffix(""), Query(result_id=result_id)
            )
            self.assertEqual(view["metadata"]["finding_count"], 1)

    def test_registration_explains_rejected_directory_and_preserves_state(self):
        with tempfile.TemporaryDirectory() as directory:
            logical, entry = scaffold(Path(directory))
            common = ("--path", str(logical), "--entry", "e001")
            selectors = ("sims-csv", "source-dir", "ngs-interpolator", "mastsel-config")
            for name in selectors:
                (entry / "data" / name).write_text("retained input\n")
                added = run_log(
                    entry, "data", "add-origin", *common, name, "data/" + name
                )
                self.assertEqual(added.returncode, 0, added.stderr)
            output = entry / "data/templates"
            output.mkdir()
            (output / "member.pkl").write_bytes(b"retained member")
            (entry / "scripts/build.py").write_text(
                "raise AssertionError('must not run')"
            )
            document = entry / "e001.md"
            arguments = " ".join(f'--{name} "<{name}>"' for name in selectors)
            template = (
                "# Trial\n\n## Build\n\n`Steps:`\n\n```bash\n"
                "./pyrun {roles}scripts/build.py " + arguments
                + " --output-dir {target}\n"
                "./pyrun scripts/build.py --unrelated data/unknown.csv "
                "--output data/unrelated.csv\n```\n\n`Results:`\n\nRetained output.\n"
            )
            before = (entry / "data.json").read_bytes()
            for target in ("data/templates", '"<templates>"'):
                document.write_text(template.format(roles="", target=target))
                authored = document.read_bytes()
                if target == "data/templates":
                    dry = run_log(
                        entry, "data", "add-generated", *common,
                        "--kind", "directory", "--pending-confirmation", "--dry-run",
                        "templates", "data/templates",
                    )
                    self.assertEqual(dry.returncode, 2, dry.stderr)
                    self.assertIn("Diagnostic not cached (--dry-run)", dry.stdout)
                    self.assertFalse(
                        (logical / ".cache/research-log-inspection.sqlite3").exists()
                    )
                failed = run_log(
                    entry, "data", "add-generated", *common,
                    "--kind", "directory", "--pending-confirmation",
                    "templates", "data/templates",
                )
                self.assertEqual(failed.returncode, 2, failed.stderr)
                self.assertFalse(failed.stdout.startswith("{"))
                self.assertIn("producer.missing", failed.stderr)
                self.assertIn("no validation performed", failed.stdout)
                result_id = inspect_result(
                    logical, Query(action="list", kind="diagnostic")
                )["items"][0]["result_id"]
                commands = inspect_result(
                    logical, Query(result_id=result_id, view="commands")
                )["items"]
                self.assertEqual(len(commands), 1)
                diagnostic = commands[0]
                self.assertEqual(diagnostic["status"], "rejected")
                self.assertEqual(diagnostic["code"], "material.candidate.unresolved")
                self.assertEqual(
                    {item["selector"] for item in diagnostic["arguments"]},
                    set(selectors),
                )
                self.assertIn("fence 1, command 1", failed.stderr)
                self.assertIn("--other-inputs", failed.stderr)
                self.assertNotIn("unrelated.csv", failed.stderr)
                self.assertEqual((entry / "data.json").read_bytes(), before)
                self.assertEqual(document.read_bytes(), authored)
            # The same directory declaration is admitted once roles are explicit.
            document.write_text(template.format(
                roles="--other-inputs " + ",".join(selectors) + " -- ",
                target='"<templates>"',
            ))
            accepted = run_log(
                entry, "data", "add-generated", *common,
                "--kind", "directory", "--pending-confirmation",
                "templates", "data/templates",
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual((output / "member.pkl").read_bytes(), b"retained member")

    def test_output_matching_is_exact_or_directory_owned(self):
        index = RejectedProducerIndex()
        command = {
            "identity": "rejected",
            "declared_outputs": [
                {"path": "/entry/data/templates", "kind": "directory"},
                {"path": "/entry/data/result.csv", "kind": "file"},
            ],
        }
        index.add({"rejected_command": command})
        for target in (
            "/entry/data/templates", "/entry/data/templates/member.pkl",
            "/entry/data/templates/nested/member.pkl", "/entry/data/result.csv",
        ):
            self.assertEqual(index.related(target), (command,))
        for target in (
            "/entry/data", "/entry/data/templates-other/member.pkl",
            "/entry/data/result.csv/child", "/another/data/templates/member.pkl",
        ):
            self.assertFalse(index.related(target))

    def test_validation_finding_and_command_views_expose_discovery_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, document = mechanical_log(root)
            document.write_text(
                document.read_text().replace("--input-catalog", "--catalog")
            )
            logical = summary.with_suffix("")
            validated = run_log(root, "validate", "--path", str(logical))
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertNotIn("results.store.malformed", validated.stderr)
            result_id = inspect_result(logical, Query(action="list"))["items"][0][
                "result_id"
            ]
            finding_id = "entry:e001:command:1:1"
            shown = run_log(
                root, "results", "finding", "--path", str(logical),
                "--id", result_id, "--finding", finding_id,
            )
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertIn("material.candidate.unresolved", shown.stdout)
            self.assertIn("selector: catalog", shown.stdout)
            self.assertIn("results command", shown.stdout)
            command = run_log(
                root, "results", "command", "--path", str(logical),
                "--id", result_id, "--command", finding_id,
            )
            self.assertEqual(command.returncode, 0, command.stderr)
            self.assertIn("status: rejected", command.stdout)
            self.assertIn("declared_outputs", command.stdout)
            missing = run_log(
                root, "results", "show", "--path", str(logical), "--id", result_id,
                "--view", "findings", "--code", "producer.missing",
            )
            self.assertEqual(missing.returncode, 0, missing.stderr)
            self.assertIn("rejected_commands", missing.stdout)
            self.assertIn("selector: catalog", missing.stdout)
            # All inspection is snapshot-based, even if the source is now unreadable.
            document.write_bytes(b"\xff")
            repeated = run_log(
                root, "results", "command", "--path", str(logical),
                "--id", result_id, "--command", finding_id,
            )
            self.assertEqual(repeated.stdout, command.stdout)

    def test_downstream_lineage_finding_names_rejected_upstream_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, document = mechanical_log(root)
            entry = document.parent
            logical = summary.with_suffix("")
            (entry / "data/raw.csv").write_text("retained input\n")
            registry = json.loads((entry / "data.json").read_text())
            registry["inputs"].append(build_local_input(
                "raw", "file", "data/raw.csv", entry_root=entry, origin=True,
            ).as_dict())
            next(item for item in registry["inputs"] if item["name"] == "catalog")[
                "origin"
            ] = False
            (entry / "data.json").write_text(json.dumps(registry))
            document.write_text(document.read_text().replace(
                "```bash\n",
                "```bash\n./pyrun scripts/model.py --configuration '<raw>' "
                "--output '<catalog>'\n",
            ))
            validated = run_log(root, "validate", "--path", str(logical))
            self.assertEqual(validated.returncode, 0, validated.stderr)
            result_id = inspect_result(logical, Query(action="list"))["items"][0][
                "result_id"
            ]
            shown = run_log(
                root, "results", "show", "--path", str(logical), "--id", result_id,
                "--view", "findings", "--code", "lineage.missing",
            )
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertIn("rejected_commands", shown.stdout)
            self.assertIn("selector: configuration", shown.stdout)
            self.assertIn("entry:e001:command:1:1", shown.stdout)


if __name__ == "__main__":
    unittest.main()
