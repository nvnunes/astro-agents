"""Frozen comparison-context contract coverage."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from log_commands.context import EntryContext
from log_commands.reproduction_comparison import compare_current_execution_outputs
from log_commands.reproduction_contract import ReproductionPlan, accepted_comparison
from log_commands.reproduction_execution import (
    ExecutionAttempt,
    ExecutionCheckpoint,
    ReproductionWorkspace,
    current_execution_attempts,
)
from log_commands.reproduction_job_storage import AcceptedJob, create_job
from log_commands.reproduction_paths import canonical_run_path, run_leaf
from reproduction_fixed_plan_test_support import accepted_plan
from test_log_reproduction_planning import _Fixture, _plan


class ReproductionComparisonTests(unittest.TestCase):
    def test_current_comparison_persists_explicit_baseline_inability(self) -> None:
        from log_commands.reproduction_comparison import (
            compare_current_execution_outputs,
        )
        from log_commands.reproduction_job_storage import load_publication_projection
        from test_reproduction_job_storage import _job_fixture, _start_and_finish

        with _job_fixture(executions=1) as (
            project,
            fixture,
            entry,
            plan,
            accepted,
            run_root,
        ):
            _start_and_finish(run_root, plan, 0)
            workspace = ReproductionWorkspace(
                accepted.run_id,
                run_root,
                project,
                run_root / "workspace",
                run_root / "runtime",
                run_root / "diagnostics",
                run_root / "executions",
            )
            for path in (
                workspace.work_project,
                workspace.runtime_root,
                workspace.diagnostics_root,
                workspace.staging_root,
            ):
                path.mkdir(exist_ok=True)
            execution = plan.executions[0]
            cid = str(execution["cid"])
            identity = str(execution["execution_id"])
            private = (
                workspace.staging_root
                / entry.id
                / cid
                / identity.rsplit(":", 1)[-1]
                / entry.root.relative_to(project)
                / "data"
                / "output-00.txt"
            )
            private.parent.mkdir(parents=True)
            private.write_text("output-00\n", encoding="utf-8")
            retained = entry.root / "data" / "output-00.txt"
            retained.write_text("changed after acceptance\n", encoding="utf-8")
            attempt = current_execution_attempts(fixture.log, plan, workspace)[0]
            comparison = compare_current_execution_outputs(
                fixture.log,
                plan,
                workspace,
                attempt,
                recorded_at="2030-01-01T00:01:00Z",
            )
            self.assertEqual(
                (comparison.artifacts[0].outcome, comparison.artifacts[0].reason),
                ("comparison_failed", "baseline_changed"),
            )
            stored = load_publication_projection(run_root).comparisons[0]
            self.assertEqual(stored.artifacts[0].outcome, "comparison_failed")
            self.assertEqual(stored.artifacts[0].reason, "baseline_changed")

    def test_empty_context_has_no_live_comparison_fallback(self) -> None:
        comparison = accepted_comparison(
            accepted_plan(),
            "e001",
            "fixture",
            "pyrun-exec/v2:" + "0" * 64,
            "data/out",
        )
        self.assertIsNone(comparison)

    def test_changed_retained_baseline_fails_even_when_regenerated_bytes_match(
        self,
    ) -> None:
        """A fixed plan must never compare against a silently replaced baseline."""

        from test_reproduction_job_storage import _start_and_finish

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
            run_root, workspace = _current_workspace(fixture, entry, plan)
            _start_and_finish(run_root, plan, 0)

            # Both present and regenerated values agree, but neither is the
            # accepted output observation frozen in the plan.
            output.write_text("replaced\n", encoding="utf-8")
            regenerated = _private_output(workspace, entry, "build", identity, output)
            regenerated.parent.mkdir(parents=True, exist_ok=True)
            regenerated.write_text("replaced\n", encoding="utf-8")
            comparison = compare_current_execution_outputs(
                fixture.log,
                plan,
                workspace,
                _current_attempt(entry.id, "build", identity),
                recorded_at="2030-01-01T00:01:00Z",
            )

            self.assertFalse(comparison.matched)
            self.assertEqual(comparison.artifacts[0].reason, "baseline_changed")

    def test_evidence_scoped_match_still_rejects_a_replaced_retained_baseline(
        self,
    ) -> None:
        """Tolerance is downstream of the accepted retained-byte baseline."""

        from test_reproduction_job_storage import _start_and_finish

        with tempfile.TemporaryDirectory() as directory:
            fixture, entry, output, identity, plan = _evidence_scoped_fixture(
                Path(directory)
            )
            run_root, workspace = _current_workspace(fixture, entry, plan)
            _start_and_finish(run_root, plan, 0)
            regenerated = _private_output(workspace, entry, "build", identity, output)
            regenerated.parent.mkdir(parents=True, exist_ok=True)
            regenerated.write_text("stable\nruntime 2\n", encoding="utf-8")
            attempt = _current_attempt(entry.id, "build", identity)

            evidence_match = compare_current_execution_outputs(
                fixture.log,
                plan,
                workspace,
                attempt,
                recorded_at="2030-01-01T00:01:00Z",
            )
            self.assertTrue(evidence_match.matched)
            self.assertEqual(evidence_match.artifacts[0].profile, "evidence")

            # The selected text is still identical and would satisfy the frozen
            # evidence definition, but the retained artifact no longer matches
            # the observation accepted into this plan.
            output.write_text("stable\nruntime replaced\n", encoding="utf-8")
            # Use a second current job because comparison rows are immutable.
            second_root, second_workspace = _current_workspace(
                fixture, entry, plan, suffix="second"
            )
            _start_and_finish(second_root, plan, 0)
            second_regenerated = _private_output(
                second_workspace, entry, "build", identity, output
            )
            second_regenerated.parent.mkdir(parents=True, exist_ok=True)
            second_regenerated.write_text("stable\nruntime 2\n", encoding="utf-8")
            replaced = compare_current_execution_outputs(
                fixture.log,
                plan,
                second_workspace,
                _current_attempt(entry.id, "build", identity),
                recorded_at="2030-01-01T00:01:00Z",
            )
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


def _current_workspace(
    fixture: _Fixture,
    entry: EntryContext,
    plan: ReproductionPlan,
    *,
    suffix: str = "first",
) -> tuple[Path, ReproductionWorkspace]:
    project = fixture.root.resolve()
    run_id = f"reproduce-20300101t000000z-comparison-{suffix}"
    logical = canonical_run_path(
        "2030-01-01T00:00:00Z",
        run_leaf(fixture.log_root.name, entry.id, run_id),
    )
    root = project / logical
    root.mkdir(parents=True)
    create_job(
        root,
        AcceptedJob(
            run_id,
            plan,
            "2030-01-01T00:00:00Z",
            logical.as_posix(),
        ),
    )
    workspace = ReproductionWorkspace(
        run_id,
        root,
        project,
        root / "workspace",
        root / "runtime",
        root / "diagnostics",
        root / "executions",
    )
    for path in (
        workspace.work_project,
        workspace.runtime_root,
        workspace.diagnostics_root,
        workspace.staging_root,
    ):
        path.mkdir(exist_ok=True)
    return root, workspace


def _private_output(
    workspace: ReproductionWorkspace,
    entry: EntryContext,
    cid: str,
    identity: str,
    output: Path,
) -> Path:
    return (
        workspace.staging_root
        / entry.id
        / cid
        / identity.rsplit(":", 1)[-1]
        / entry.root.resolve().relative_to(workspace.source_project.resolve())
        / output.resolve().relative_to(entry.root.resolve())
    )


def _current_attempt(entry: str, cid: str, identity: str) -> ExecutionAttempt:
    checkpoint = ExecutionCheckpoint(
        entry,
        cid,
        identity,
        "succeeded",
        "state.sqlite",
        "2030-01-01T00:00:01Z",
        (),
    )
    return ExecutionAttempt(
        entry,
        cid,
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
