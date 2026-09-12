"""Frozen comparison-context contract coverage."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from log_commands.reproduction_comparison import compare_execution_outputs
from log_commands.reproduction_contract import accepted_comparison
from log_commands.reproduction_execution import (
    ExecutionAttempt,
    ExecutionCheckpoint,
    ReproductionWorkspace,
)
from reproduction_fixed_plan_test_support import accepted_plan
from test_log_reproduction_planning import _Fixture, _plan


class ReproductionComparisonTests(unittest.TestCase):
    def test_empty_context_has_no_live_comparison_fallback(self) -> None:
        comparison = accepted_comparison(
            accepted_plan(), "e001", "pyrun-exec/v1:" + "0" * 64, "data/out"
        )
        self.assertIsNone(comparison)

    def test_changed_retained_baseline_fails_even_when_regenerated_bytes_match(
        self,
    ) -> None:
        """A fixed plan must never compare against a silently replaced baseline."""

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            fixture = _Fixture(project)
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            output = entry.root / "data" / "output.txt"
            raw.write_text("raw\n", encoding="utf-8")
            output.write_text("accepted\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "output", output, origin=False),
                ],
            )
            fixture.evidence(entry, "output")
            identity, execution = fixture.execution(
                entry, "build", {"raw": raw}, {"output": output}
            )
            fixture.write_pyrun(entry, [(identity, execution)])
            plan = _plan(fixture, entry)
            source_project = fixture.root.resolve()
            run_root = source_project / "run"
            work = run_root / "workspace"
            runtime = run_root / "runtime"
            diagnostics = run_root / "diagnostics"
            staging = run_root / "executions"
            for path in (work, runtime, diagnostics, staging):
                path.mkdir(parents=True)
            workspace = ReproductionWorkspace(
                "reproduce-baseline",
                run_root,
                source_project,
                work,
                runtime,
                diagnostics,
                staging,
            )

            # Both present and regenerated values agree, but neither is the
            # accepted output observation frozen in the plan.
            output.write_text("replaced\n", encoding="utf-8")
            regenerated = workspace.map_source(output)
            regenerated.parent.mkdir(parents=True, exist_ok=True)
            regenerated.write_text("replaced\n", encoding="utf-8")
            checkpoint = ExecutionCheckpoint(
                entry.id,
                identity,
                "succeeded",
                "checkpoints/fixture.json",
                "2030-01-01T00:00:01Z",
                (),
            )
            comparison = compare_execution_outputs(
                fixture.log,
                plan,
                workspace,
                ExecutionAttempt(
                    entry.id,
                    identity,
                    0,
                    False,
                    None,
                    None,
                    checkpoint,
                    (),
                    "diagnostics/out",
                    "diagnostics/err",
                ),
            )

            self.assertFalse(comparison.matched)
            self.assertEqual(comparison.artifacts[0].reason, "baseline_changed")

    def test_evidence_scoped_match_still_rejects_a_replaced_retained_baseline(
        self,
    ) -> None:
        """Tolerance is downstream of the accepted retained-byte baseline."""

        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, output, identity, plan = _evidence_scoped_fixture(
                Path(directory)
            )
            workspace = _comparison_workspace(fixture.root.resolve())
            regenerated = workspace.map_source(output)
            regenerated.parent.mkdir(parents=True, exist_ok=True)
            regenerated.write_text("stable\nruntime 2\n", encoding="utf-8")
            attempt = _attempt(entry.id, identity)

            evidence_match = compare_execution_outputs(
                fixture.log, plan, workspace, attempt
            )
            self.assertTrue(evidence_match.matched)
            self.assertEqual(evidence_match.artifacts[0].profile, "evidence")

            # The selected text is still identical and would satisfy the frozen
            # evidence definition, but the retained artifact no longer matches
            # the observation accepted into this plan.
            output.write_text("stable\nruntime replaced\n", encoding="utf-8")
            replaced = compare_execution_outputs(fixture.log, plan, workspace, attempt)
            self.assertFalse(replaced.matched)
            self.assertEqual(replaced.artifacts[0].reason, "baseline_changed")


def _evidence_scoped_fixture(
    root: Path,
) -> tuple[_Fixture, object, Path, str, object]:
    """Build a planner-produced evidence definition, never raw plan JSON."""

    fixture = _Fixture(root)
    entry = fixture.entry(1)
    raw = entry.root / "data" / "raw.txt"
    output = entry.root / "data" / "output.txt"
    raw.write_text("raw\n", encoding="utf-8")
    output.write_text("stable\nruntime 1\n", encoding="utf-8")
    fixture.write_data(entry, [fixture.item(entry, "raw", raw, origin=True)])
    identity, execution = fixture.execution(
        entry, "build", {"raw": raw}, {"output": output}
    )
    fixture.write_pyrun(entry, [(identity, execution)])
    data_path = entry.root / "data.json"
    data = __import__("json").loads(data_path.read_text(encoding="utf-8"))
    data["inputs"].append(
        {
            "comparison": {
                "contract": "research-log-evidence-scoped-comparison/1",
                "profile": "evidence",
            },
            "identity": {"algorithm": "sha256"},
            "kind": "file",
            "location": "data/output.txt",
            "name": "output",
            "origin": False,
        }
    )
    data_path.write_text(__import__("json").dumps(data), encoding="utf-8")
    (entry.root / "evidence.json").write_text(
        __import__("json").dumps(
            {
                "records": [
                    {
                        "document": f"entries/{entry.root.name}/{entry.id}.md",
                        "id": "stable-output",
                        "kind": "output",
                        "sources": [
                            {
                                "locator": {
                                    "text": {"contains": "stable", "occurrence": 1}
                                },
                                "source": "<output>",
                            }
                        ],
                        "transformation": None,
                    }
                ],
                "schema": "research-log-evidence/v4",
            }
        ),
        encoding="utf-8",
    )
    document = entry.root / f"{entry.id}.md"
    document.write_text(
        document.read_text(encoding="utf-8").replace(
            "Recorded.", "<!-- eid:stable-output -->\n```text\nstable\n```"
        ),
        encoding="utf-8",
    )
    return fixture, entry, output, identity, _plan(fixture, entry)


def _comparison_workspace(project: Path) -> ReproductionWorkspace:
    root = project / "comparison-run"
    work, runtime, diagnostics, staging = (
        root / "workspace",
        root / "runtime",
        root / "diagnostics",
        root / "executions",
    )
    for path in (work, runtime, diagnostics, staging):
        path.mkdir(parents=True)
    return ReproductionWorkspace(
        "reproduce-comparison", root, project, work, runtime, diagnostics, staging
    )


def _attempt(entry: str, identity: str) -> ExecutionAttempt:
    checkpoint = ExecutionCheckpoint(
        entry,
        identity,
        "succeeded",
        "checkpoints/fixture.json",
        "2030-01-01T00:00:01Z",
        (),
    )
    return ExecutionAttempt(
        entry,
        identity,
        0,
        False,
        None,
        None,
        checkpoint,
        (),
        "diagnostics/out",
        "diagnostics/err",
    )
