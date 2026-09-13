"""Focused durable fixed-plan job-store coverage."""
# ruff: noqa: E501

from __future__ import annotations

import fcntl
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Literal
from unittest import mock

from log_commands import reproduction_job_storage as job_storage
from log_commands.context import EntryContext
from log_commands.reproduction_contract import ReproductionPlan, ReproductionRuntime
from log_commands.reproduction_job_storage import (
    AcceptedJob,
    ArtifactComparisonWrite,
    CheckpointOutput,
    ExecutionComparisonWrite,
    ExecutionIdentity,
    ExecutionPermitAttachment,
    ExecutionStart,
    ExecutionTerminal,
    JobStoreBusyError,
    JobStoreExistsError,
    JobStoreInvariantError,
    JobStoreMalformedError,
    JobStoreMissingError,
    JobStoreSymlinkError,
    JobStoreTransitionError,
    JobStoreUnsupportedError,
    PublicationFailure,
    PublicationResumeRequest,
    RequirementEffect,
    RunFailure,
    RunOwner,
    RunResumeRequest,
    RunStopCompletion,
    RunStopRequest,
    WorkerRecord,
    attach_execution_permit,
    audit_job_state,
    begin_publication,
    begin_publication_resume,
    begin_run_resume,
    clear_execution_permit,
    clear_execution_scratch,
    create_job,
    finish_run_stop,
    load_accepted_plan,
    load_accepted_scheduling,
    load_execution_checkpoint,
    load_publication_projection,
    load_requirement_effect,
    load_run_control,
    load_run_status,
    load_scheduler_owner,
    open_locked_job,
    prepare_publication,
    recognize_run_directory,
    record_execution_comparison,
    record_execution_start,
    record_execution_terminal,
    record_publication_failure,
    record_report_commit,
    record_requirement_effect,
    record_result_commit,
    replace_execution_workers,
    replace_run_owner,
    request_run_failure,
    request_run_stop,
)
from log_commands.reproduction_paths import canonical_run_path, run_leaf
from test_log_reproduction_comparison import _evidence_scoped_fixture
from test_log_reproduction_planning import _Fixture, _plan


@contextmanager
def _job_fixture(
    *, executions: int = 2, outputs_per_execution: int = 1, create: bool = True
) -> Iterator[tuple[Path, _Fixture, EntryContext, ReproductionPlan, AcceptedJob, Path]]:
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory).resolve()
        fixture = _Fixture(project)
        entry = fixture.entry(1)
        raw = entry.root / "data" / "raw.txt"
        raw.write_text("raw\n", encoding="utf-8")
        outputs = {
            f"output-{number:02d}": entry.root / "data" / f"output-{number:02d}.txt"
            for number in range(executions * outputs_per_execution)
        }
        for name, path in outputs.items():
            path.write_text(f"{name}\n", encoding="utf-8")
        fixture.write_data(
            entry,
            [fixture.item(entry, "raw", raw, origin=True)]
            + [
                fixture.item(entry, name, path, origin=False)
                for name, path in outputs.items()
            ],
        )
        fixture.evidence(entry, *outputs)
        output_items = list(outputs.items())
        execution_records = []
        for index in range(executions):
            selected = dict(
                output_items[
                    index * outputs_per_execution : (index + 1)
                    * outputs_per_execution
                ]
            )
            execution_records.append(
                fixture.execution(entry, f"build-{index}", {"raw": raw}, selected)
            )
        fixture.write_pyrun(entry, execution_records)
        plan = _plan(
            fixture,
            entry,
            runtime=ReproductionRuntime(jobs=max(1, executions)),
        )
        accepted_at = "2030-01-01T00:00:00Z"
        run_id = "reproduce-20300101t000000z-foundation"
        leaf = run_leaf(fixture.log_root.name, entry.id, run_id)
        logical = canonical_run_path(accepted_at, leaf).as_posix()
        run_root = project / logical
        run_root.mkdir(parents=True)
        accepted = AcceptedJob(run_id, plan, accepted_at, logical)
        if create:
            create_job(run_root, accepted)
        yield project, fixture, entry, plan, accepted, run_root


def _start_and_finish(
    run_root: Path, plan: ReproductionPlan, index: int
) -> tuple[str, str, dict]:
    execution = plan.executions[index]
    entry = str(execution["entry"])
    execution_id = str(execution["execution_id"])
    record = next(
        item
        for item in plan.commands
        if item["entry"] == entry and item["execution_id"] == execution_id
    )
    state = record["execution_state"]
    assert isinstance(state, dict)
    observed = state["observed"]
    assert isinstance(observed, dict)
    output_fingerprints = observed["outputs"]
    assert isinstance(output_fingerprints, dict)
    artifact = str(execution["outputs"][0])
    attach_execution_permit(
        run_root,
        ExecutionPermitAttachment(
            entry,
            execution_id,
            f"permit-{index}",
            f"2030-01-01T00:00:{index * 2 + 1:02d}Z",
        ),
    )
    record_execution_start(
        run_root,
        ExecutionStart(
            entry,
            execution_id,
            f"permit-{index}",
            f"2030-01-01T00:00:{index * 2 + 1:02d}Z",
            f"2030-01-01T00:00:{index * 2 + 1:02d}Z",
            f"/private/tmp/reproduction-worker-{index}",
        ),
    )
    record_execution_terminal(
        run_root,
        ExecutionTerminal(
            entry,
            execution_id,
            f"permit-{index}",
            "succeeded",
            f"2030-01-01T00:00:{index * 2 + 2:02d}Z",
            f"2030-01-01T00:00:{index * 2 + 2:02d}Z",
            1.0,
            (CheckpointOutput(artifact, output_fingerprints[artifact]),),
            workers=(
                WorkerRecord(
                    f"worker-{index}",
                    None,
                    2000 + index,
                    "exited",
                    f"2030-01-01T00:00:{index * 2 + 1:02d}Z",
                    f"2030-01-01T00:00:{index * 2 + 2:02d}Z",
                ),
            ),
        ),
    )
    identity = ExecutionIdentity(entry, execution_id)
    clear_execution_permit(
        run_root,
        identity,
        f"permit-{index}",
        "succeeded",
        f"2030-01-01T00:00:{index * 2 + 2:02d}Z",
    )
    clear_execution_scratch(
        run_root, identity, f"/private/tmp/reproduction-worker-{index}"
    )
    return entry, execution_id, output_fingerprints[artifact]


def _comparison(
    plan: ReproductionPlan,
    index: int,
    *,
    outcome: Literal[
        "matched", "changed", "failed", "comparison_failed", "skipped"
    ] = "matched",
    recorded_at: str = "2030-01-01T00:01:00Z",
) -> ExecutionComparisonWrite:
    execution = plan.executions[index]
    entry = str(execution["entry"])
    execution_id = str(execution["execution_id"])
    record = next(
        item
        for item in plan.commands
        if item["entry"] == entry and item["execution_id"] == execution_id
    )
    state = record["execution_state"]
    assert isinstance(state, dict)
    observed = state["observed"]
    assert isinstance(observed, dict)
    outputs = observed["outputs"]
    assert isinstance(outputs, dict)
    artifact = str(execution["outputs"][0])
    baseline = outputs[artifact]
    assert isinstance(baseline, dict)
    return ExecutionComparisonWrite(
        entry,
        execution_id,
        True,
        10,
        f"workspace/{entry}/{index}",
        (f"diagnostics/{entry}/{index}.log",),
        (
            ArtifactComparisonWrite(
                artifact,
                "file",
                True,
                f"workspace/{entry}/{index}/{artifact}",
                outcome,
                None if outcome == "matched" else "content_changed",
                "text",
                baseline,
                baseline,
                None,
                baseline,
            ),
        ),
        recorded_at,
    )


def _start_and_fail(run_root: Path, plan: ReproductionPlan, index: int) -> None:
    execution = plan.executions[index]
    entry = str(execution["entry"])
    execution_id = str(execution["execution_id"])
    permit_id = f"permit-failed-{index}"
    attach_execution_permit(
        run_root,
        ExecutionPermitAttachment(
            entry, execution_id, permit_id, "2030-01-01T00:00:01Z"
        ),
    )
    record_execution_start(
        run_root,
        ExecutionStart(
            entry,
            execution_id,
            permit_id,
            "2030-01-01T00:00:01Z",
            "2030-01-01T00:00:01Z",
            f"/private/tmp/reproduction-failed-{index}",
        ),
    )
    record_execution_terminal(
        run_root,
        ExecutionTerminal(
            entry,
            execution_id,
            permit_id,
            "failed",
            "2030-01-01T00:00:02Z",
            "2030-01-01T00:00:02Z",
            1.0,
            failure_code="execution_failed",
            failure_message="fixture failure",
            failure_recorded_at="2030-01-01T00:00:02Z",
        ),
    )
    identity = ExecutionIdentity(entry, execution_id)
    clear_execution_permit(
        run_root,
        identity,
        permit_id,
        "failed",
        "2030-01-01T00:00:02Z",
    )
    clear_execution_scratch(
        run_root, identity, f"/private/tmp/reproduction-failed-{index}"
    )


class ReproductionJobStorageMilestoneTests(unittest.TestCase):
    def test_policy_command_with_null_source_digest_round_trips(self) -> None:
        with _job_fixture(executions=1, create=False) as (
            *_unused,
            plan,
            accepted,
            run_root,
        ):
            commands = list(plan.commands)
            commands[0] = dict(
                commands[0],
                selection="policy",
                prior_disposition=None,
                source_digest=None,
            )
            policy_plan = replace(plan, commands=tuple(commands))
            create_job(run_root, replace(accepted, plan=policy_plan))
            self.assertEqual(
                load_accepted_plan(run_root).serialized(),
                policy_plan.serialized(),
            )

    def test_accept_reopen_and_checkpoint_two_execution_plan(self) -> None:
        """One immutable plan and one checkpoint survive a complete reopen."""

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            fixture = _Fixture(project)
            entry = fixture.entry(1)
            raw = entry.root / "data" / "raw.txt"
            producer = entry.root / "data" / "producer.txt"
            consumer = entry.root / "data" / "consumer.txt"
            raw.write_text("raw\n", encoding="utf-8")
            producer.write_text("producer\n", encoding="utf-8")
            consumer.write_text("consumer\n", encoding="utf-8")
            fixture.write_data(
                entry,
                [
                    fixture.item(entry, "raw", raw, origin=True),
                    fixture.item(entry, "producer", producer, origin=False),
                    fixture.item(entry, "consumer", consumer, origin=False),
                ],
            )
            fixture.evidence(entry, "consumer")
            producer_id, producer_execution = fixture.execution(
                entry, "producer", {"raw": raw}, {"producer": producer}
            )
            consumer_id, consumer_execution = fixture.execution(
                entry,
                "consumer",
                {"producer": producer},
                {"consumer": consumer},
            )
            fixture.write_pyrun(
                entry,
                [
                    (producer_id, producer_execution),
                    (consumer_id, consumer_execution),
                ],
            )
            plan = _plan(
                fixture,
                entry,
                runtime=ReproductionRuntime(jobs=2),
            )
            self.assertEqual(len(plan.executions), 2)

            accepted_at = "2030-01-01T00:00:00Z"
            run_id = "reproduce-20300101t000000z-foundation"
            leaf = run_leaf(fixture.log_root.name, entry.id, run_id)
            logical = canonical_run_path(accepted_at, leaf).as_posix()
            run_root = project / logical
            run_root.mkdir(parents=True)
            accepted = AcceptedJob(run_id, plan, accepted_at, logical)

            identity = create_job(run_root, accepted)
            self.assertEqual(identity.run_id, run_id)
            self.assertEqual(
                load_accepted_plan(run_root).serialized(), plan.serialized()
            )
            first_execution = plan.executions[0]
            scheduling = load_accepted_scheduling(
                run_root,
                ExecutionIdentity(
                    str(first_execution["entry"]),
                    str(first_execution["execution_id"]),
                ),
            )
            self.assertEqual(scheduling.run_id, run_id)
            self.assertEqual(scheduling.plan_order, first_execution["order"])
            self.assertEqual(scheduling.kind, "ordinary")
            self.assertEqual(
                scheduling.read_paths, tuple(first_execution["read_paths"])
            )
            self.assertEqual(scheduling.write_paths, tuple(first_execution["write_paths"])
            )
            self.assertEqual(scheduling.run_path, first_execution["run_path"])
            self.assertEqual(
                scheduling.writable_paths,
                tuple(first_execution["writable_paths"]),
            )
            initial = load_run_status(run_root)
            self.assertEqual(initial.phase, "accepted")
            self.assertEqual(initial.total_executions, 2)
            self.assertEqual(initial.checkpoints, ())

            database = run_root / "state.sqlite"
            before_second_acceptance = database.read_bytes()
            with self.assertRaises(JobStoreExistsError):
                create_job(run_root, replace(accepted, plan=replace(plan, jobs=1)))
            self.assertEqual(database.read_bytes(), before_second_acceptance)
            self.assertEqual(
                load_accepted_plan(run_root).serialized(), plan.serialized()
            )

            planned = plan.executions[0]
            execution_id = str(planned["execution_id"])
            record = next(
                item for item in plan.commands if item["execution_id"] == execution_id
            )
            observed = record["execution_state"]
            assert isinstance(observed, dict)
            observations = observed["observed"]
            assert isinstance(observations, dict)
            output_fingerprints = observations["outputs"]
            assert isinstance(output_fingerprints, dict)
            artifact = str(planned["outputs"][0])

            attach_execution_permit(
                run_root,
                ExecutionPermitAttachment(
                    entry.id,
                    execution_id,
                    "permit-foundation-1",
                    "2030-01-01T00:00:01Z",
                ),
            )
            record_execution_start(
                run_root,
                ExecutionStart(
                    entry.id,
                    execution_id,
                    "permit-foundation-1",
                    "2030-01-01T00:00:01Z",
                    "2030-01-01T00:00:01Z",
                    "/private/tmp/reproduction-foundation-1",
                    stdout_path="diagnostics/producer.stdout",
                    stderr_path="diagnostics/producer.stderr",
                ),
            )
            record_execution_terminal(
                run_root,
                ExecutionTerminal(
                    entry.id,
                    execution_id,
                    "permit-foundation-1",
                    "succeeded",
                    "2030-01-01T00:00:03Z",
                    "2030-01-01T00:00:03Z",
                    2.0,
                    (
                        CheckpointOutput(
                            artifact,
                            output_fingerprints[artifact],
                        ),
                    ),
                ),
            )

            reopened_plan = load_accepted_plan(run_root)
            reopened = load_run_status(run_root)
            self.assertEqual(reopened_plan.serialized(), plan.serialized())
            self.assertEqual(reopened.completed_executions, 1)
            self.assertEqual(len(reopened.checkpoints), 1)
            checkpoint = reopened.checkpoints[0]
            self.assertEqual(
                (checkpoint.entry, checkpoint.execution_id), (entry.id, execution_id)
            )
            self.assertEqual(checkpoint.state, "succeeded")
            self.assertEqual(checkpoint.outputs[0].artifact, artifact)
            self.assertEqual(
                dict(checkpoint.outputs[0].fingerprint),
                output_fingerprints[artifact],
            )


class ReproductionJobStorageSchemaTests(unittest.TestCase):
    def test_run_directory_recognition_reads_no_historical_json(self) -> None:
        with _job_fixture() as (*_unused, run_root):
            self.assertEqual(recognize_run_directory(run_root), "current")
            historical = run_root.parent / "reproduce-historical"
            historical.mkdir()
            legacy = historical / "run.json"
            legacy.write_text("not valid json", encoding="utf-8")
            self.assertEqual(
                recognize_run_directory(historical), "historical_unsupported"
            )
            self.assertEqual(legacy.read_text(encoding="utf-8"), "not valid json")
            self.assertEqual(
                recognize_run_directory(run_root.parent / "not-a-run"), "absent"
            )

    def test_schema_has_exact_normalized_authority_shape(self) -> None:
        expected = {
            "runs": "run_id summary target_kind target_entry include_all jobs execution_timeout_seconds accepted_at run_path workspace_path diagnostics_path",
            "run_state": "run_id status phase stop_requested_at started_at resumed_at stopped_at finished_at updated_at completed_executions matched changed failed comparison_failed skipped latest_execution_entry latest_execution_id latest_execution_code latest_execution_message latest_execution_recorded_at operational_code operational_message operational_recorded_at",
            "accepted_admission": "run_id validation_id validation_result_id rules_version evaluated_at",
            "accepted_admission_groups": "run_id disposition position entry group_id decision_json",
            "accepted_commands": "run_id command_pk entry execution_id selection auto_reproduce exclusive queued accepted_requires_reproduction prior_disposition source_digest entry_root project_root cwd script last_run_at runner environment_profile execution_contract details_json data_declaration_json",
            "accepted_recipe_parameters": "run_id command_pk position value",
            "accepted_parameter_roles": "run_id command_pk selector role",
            "accepted_recipe_environment": "run_id command_pk name value",
            "accepted_materials": "run_id material_pk role identity kind selection_json fingerprint_json",
            "accepted_command_inputs": "run_id command_pk name material_pk",
            "accepted_command_outputs": "run_id command_pk artifact kind material_pk",
            "accepted_command_code": "run_id command_pk path role material_pk",
            "accepted_comparison_materials": "run_id position material_pk",
            "accepted_executions": "run_id command_pk plan_order run_path",
            "accepted_execution_dependencies": "run_id command_pk dependency_command_pk position",
            "accepted_execution_outputs": "run_id command_pk output_command_position artifact",
            "accepted_execution_claims": "run_id command_pk claim_kind position path",
            "accepted_cases": "run_id position entry artifact command_pk disposition reason",
            "accepted_boundaries": "run_id position kind entry name artifact selection_json fingerprint_json",
            "accepted_failures": "run_id position entry artifact outcome reason dependencies_json",
            "accepted_comparisons": "run_id command_pk artifact definition_identity",
            "accepted_comparison_records": "run_id command_pk artifact position record_json",
            "accepted_evidence_observations": "run_id observation_pk entry record_id resource kind selection_json fingerprint_json",
            "accepted_evidence_comparison_links": "run_id observation_pk definition_identity",
            "run_owner": "run_id supervisor_pid state registered_at last_observed_at",
            "execution_checkpoints": "run_id command_pk state permit_id released_permit_id checkpointed_at started_at finished_at elapsed_seconds failure_code failure_message failure_recorded_at stdout_path stderr_path scratch_path",
            "checkpoint_outputs": "run_id command_pk artifact fingerprint_json",
            "workers": "run_id worker_id parent_worker_id command_pk pid state registered_at last_observed_at",
            "execution_effects": "run_id command_pk comparison_recorded_at requirement_cleared_at",
            "staged_executions": "run_id command_pk complete retained_bytes workspace_path",
            "staged_diagnostics": "run_id command_pk position path",
            "artifact_comparisons": "run_id command_pk artifact kind available staged_path outcome reason profile expected_json regenerated_json evidence_definition",
            "artifact_comparison_evidence": "run_id command_pk artifact position record_id retained_json regenerated_json tolerance_json matched",
            "publication_state": "run_id stage publication_identity result_generation report_generation failure_code failure_message failure_recorded_at updated_at",
        }
        single_primary_keys = {
            "runs",
            "run_state",
            "accepted_admission",
            "run_owner",
            "publication_state",
        }
        without_rowid = set(expected) - single_primary_keys - {"execution_checkpoints"}
        primary_keys = {
            "runs": ("run_id",),
            "run_state": ("run_id",),
            "accepted_admission": ("run_id",),
            "accepted_admission_groups": ("run_id", "disposition", "position"),
            "accepted_commands": ("run_id", "command_pk"),
            "accepted_recipe_parameters": ("run_id", "command_pk", "position"),
            "accepted_parameter_roles": ("run_id", "command_pk", "selector"),
            "accepted_recipe_environment": ("run_id", "command_pk", "name"),
            "accepted_materials": ("run_id", "material_pk"),
            "accepted_command_inputs": ("run_id", "command_pk", "name"),
            "accepted_command_outputs": ("run_id", "command_pk", "artifact"),
            "accepted_command_code": ("run_id", "command_pk", "path"),
            "accepted_comparison_materials": ("run_id", "position"),
            "accepted_executions": ("run_id", "command_pk"),
            "accepted_execution_dependencies": ("run_id", "command_pk", "position"),
            "accepted_execution_outputs": (
                "run_id",
                "command_pk",
                "output_command_position",
            ),
            "accepted_execution_claims": (
                "run_id",
                "command_pk",
                "claim_kind",
                "position",
            ),
            "accepted_cases": ("run_id", "position"),
            "accepted_boundaries": ("run_id", "position"),
            "accepted_failures": ("run_id", "position"),
            "accepted_comparisons": ("run_id", "command_pk", "artifact"),
            "accepted_comparison_records": (
                "run_id",
                "command_pk",
                "artifact",
                "position",
            ),
            "accepted_evidence_observations": ("run_id", "observation_pk"),
            "accepted_evidence_comparison_links": (
                "run_id",
                "observation_pk",
                "definition_identity",
            ),
            "run_owner": ("run_id",),
            "execution_checkpoints": ("run_id", "command_pk"),
            "checkpoint_outputs": ("run_id", "command_pk", "artifact"),
            "workers": ("run_id", "worker_id"),
            "execution_effects": ("run_id", "command_pk"),
            "staged_executions": ("run_id", "command_pk"),
            "staged_diagnostics": ("run_id", "command_pk", "position"),
            "artifact_comparisons": ("run_id", "command_pk", "artifact"),
            "artifact_comparison_evidence": (
                "run_id",
                "command_pk",
                "artifact",
                "position",
            ),
            "publication_state": ("run_id",),
        }
        run_fk = {("runs", (("run_id", "run_id"),), "RESTRICT")}
        command_fk = (
            "accepted_commands",
            (("run_id", "run_id"), ("command_pk", "command_pk")),
            "RESTRICT",
        )
        execution_fk = (
            "accepted_executions",
            (("run_id", "run_id"), ("command_pk", "command_pk")),
            "RESTRICT",
        )
        material_fk = (
            "accepted_materials",
            (("run_id", "run_id"), ("material_pk", "material_pk")),
            "RESTRICT",
        )
        foreign_keys = {
            "runs": set(),
            "run_state": run_fk,
            "accepted_admission": run_fk,
            "accepted_admission_groups": {
                ("accepted_admission", (("run_id", "run_id"),), "RESTRICT")
            },
            "accepted_commands": run_fk,
            "accepted_recipe_parameters": {command_fk},
            "accepted_parameter_roles": {command_fk},
            "accepted_recipe_environment": {command_fk},
            "accepted_materials": run_fk,
            "accepted_command_inputs": {command_fk, material_fk},
            "accepted_command_outputs": {command_fk, material_fk},
            "accepted_command_code": {command_fk, material_fk},
            "accepted_comparison_materials": {material_fk},
            "accepted_executions": {command_fk},
            "accepted_execution_dependencies": {
                execution_fk,
                (
                    "accepted_executions",
                    (
                        ("run_id", "run_id"),
                        ("dependency_command_pk", "command_pk"),
                    ),
                    "RESTRICT",
                ),
            },
            "accepted_execution_outputs": {execution_fk},
            "accepted_execution_claims": {execution_fk},
            "accepted_cases": {command_fk, *run_fk},
            "accepted_boundaries": run_fk,
            "accepted_failures": run_fk,
            "accepted_comparisons": {command_fk},
            "accepted_comparison_records": {
                (
                    "accepted_comparisons",
                    (
                        ("run_id", "run_id"),
                        ("command_pk", "command_pk"),
                        ("artifact", "artifact"),
                    ),
                    "RESTRICT",
                )
            },
            "accepted_evidence_observations": run_fk,
            "accepted_evidence_comparison_links": {
                (
                    "accepted_evidence_observations",
                    (("run_id", "run_id"), ("observation_pk", "observation_pk")),
                    "RESTRICT",
                ),
                (
                    "accepted_comparisons",
                    (
                        ("run_id", "run_id"),
                        ("definition_identity", "definition_identity"),
                    ),
                    "RESTRICT",
                ),
            },
            "run_owner": run_fk,
            "execution_checkpoints": {execution_fk},
            "checkpoint_outputs": {
                (
                    "execution_checkpoints",
                    (("run_id", "run_id"), ("command_pk", "command_pk")),
                    "CASCADE",
                ),
                (
                    "accepted_command_outputs",
                    (
                        ("run_id", "run_id"),
                        ("command_pk", "command_pk"),
                        ("artifact", "artifact"),
                    ),
                    "RESTRICT",
                ),
            },
            "workers": {
                *run_fk,
                execution_fk,
                (
                    "workers",
                    (("run_id", "run_id"), ("parent_worker_id", "worker_id")),
                    "NO ACTION",
                ),
            },
            "execution_effects": {execution_fk},
            "staged_executions": {execution_fk},
            "staged_diagnostics": {
                (
                    "staged_executions",
                    (("run_id", "run_id"), ("command_pk", "command_pk")),
                    "CASCADE",
                )
            },
            "artifact_comparisons": {
                (
                    "staged_executions",
                    (("run_id", "run_id"), ("command_pk", "command_pk")),
                    "CASCADE",
                ),
                (
                    "accepted_command_outputs",
                    (
                        ("run_id", "run_id"),
                        ("command_pk", "command_pk"),
                        ("artifact", "artifact"),
                    ),
                    "RESTRICT",
                ),
            },
            "artifact_comparison_evidence": {
                (
                    "artifact_comparisons",
                    (
                        ("run_id", "run_id"),
                        ("command_pk", "command_pk"),
                        ("artifact", "artifact"),
                    ),
                    "CASCADE",
                )
            },
            "publication_state": run_fk,
        }
        unique_keys = {
            "accepted_commands": {("run_id", "entry", "execution_id")},
            "accepted_materials": {
                ("run_id", "role", "identity", "fingerprint_json")
            },
            "accepted_executions": {("run_id", "plan_order")},
            "accepted_execution_outputs": {("run_id", "command_pk", "artifact")},
            "accepted_execution_claims": {
                ("run_id", "command_pk", "claim_kind", "path")
            },
            "accepted_comparisons": {("run_id", "definition_identity")},
            "accepted_evidence_observations": {
                ("run_id", "entry", "record_id", "resource")
            },
        }
        check_counts = {
            "runs": 5,
            "run_state": 11,
            "accepted_admission_groups": 2,
            "accepted_commands": 9,
            "accepted_recipe_parameters": 1,
            "accepted_parameter_roles": 1,
            "accepted_materials": 2,
            "accepted_command_outputs": 1,
            "accepted_command_code": 1,
            "accepted_comparison_materials": 1,
            "accepted_executions": 1,
            "accepted_execution_dependencies": 1,
            "accepted_execution_outputs": 1,
            "accepted_execution_claims": 2,
            "accepted_cases": 1,
            "accepted_boundaries": 2,
            "accepted_failures": 2,
            "accepted_comparison_records": 1,
            "accepted_evidence_observations": 1,
            "run_owner": 2,
            "execution_checkpoints": 3,
            "workers": 2,
            "staged_executions": 2,
            "staged_diagnostics": 1,
            "artifact_comparisons": 2,
            "artifact_comparison_evidence": 2,
            "publication_state": 5,
        }
        check_fragments = {
            "runs": (
                "check(target_kind in ('log', 'entry'))",
                "check(include_all in (0, 1))",
                "check(jobs >= 1)",
                "check(execution_timeout_seconds >= 1)",
                "check((target_kind = 'log' and target_entry is null) or (target_kind = 'entry' and target_entry is not null))",
            ),
            "run_state": (
                "check(status in ('complete', 'stopped', 'failed'))",
                "check(phase in ('accepted', 'planning', 'preflight', 'executing', 'comparing', 'publishing', 'stopping'))",
                "check(completed_executions >= 0)",
                "check(matched >= 0)",
                "check(changed >= 0)",
                "check(failed >= 0)",
                "check(comparison_failed >= 0)",
                "check(skipped >= 0)",
                "check((status is null and phase is not null) or (status is not null and phase is null))",
                "check((latest_execution_entry is null and latest_execution_id is null and latest_execution_code is null and latest_execution_message is null and latest_execution_recorded_at is null) or (latest_execution_entry is not null and latest_execution_id is not null and latest_execution_code is not null and latest_execution_message is not null and latest_execution_recorded_at is not null))",
                "check((operational_code is null and operational_message is null and operational_recorded_at is null) or (operational_code is not null and operational_message is not null and operational_recorded_at is not null))",
            ),
            "accepted_admission_groups": (
                "check(disposition in ('admitted', 'excluded'))",
                "check(position >= 0)",
            ),
            "accepted_commands": (
                "check(command_pk >= 1)",
                "check(selection in ('run', 'not_needed', 'unchanged', 'blocked', 'policy'))",
                "check(auto_reproduce in (0, 1))",
                "check(exclusive in (0, 1))",
                "check(queued in (0, 1))",
                "check(accepted_requires_reproduction in (0, 1))",
                "check(prior_disposition in ('failed', 'blocked'))",
                "check((selection = 'unchanged' and prior_disposition is not null) or (selection <> 'unchanged' and prior_disposition is null))",
                "check(selection in ('not_needed', 'policy') or source_digest is not null)",
            ),
            "accepted_recipe_parameters": ("check(position >= 0)",),
            "accepted_parameter_roles": (
                "check(role in ('input', 'output', 'ordinary'))",
            ),
            "accepted_materials": (
                "check(material_pk >= 1)",
                "check(role in ('boundary', 'comparison_baseline', 'input', 'script', 'code'))",
            ),
            "accepted_command_outputs": ("check(kind in ('file', 'directory'))",),
            "accepted_command_code": ("check(role in ('script', 'code'))",),
            "accepted_comparison_materials": ("check(position >= 0)",),
            "accepted_executions": ("check(plan_order >= 1)",),
            "accepted_execution_dependencies": ("check(position >= 0)",),
            "accepted_execution_outputs": ("check(output_command_position >= 0)",),
            "accepted_execution_claims": (
                "check(claim_kind in ('read', 'write', 'writable'))",
                "check(position >= 0)",
            ),
            "accepted_cases": ("check(position >= 0)",),
            "accepted_boundaries": (
                "check(position >= 0)",
                "check(kind in ('origin', 'cross_entry', 'non_automatic', 'outside_queue'))",
            ),
            "accepted_failures": (
                "check(position >= 0)",
                "check(outcome = 'failed')",
            ),
            "accepted_comparison_records": ("check(position >= 0)",),
            "accepted_evidence_observations": ("check(observation_pk >= 1)",),
            "run_owner": (
                "check(supervisor_pid >= 1)",
                "check(state in ('running', 'stopped', 'exited'))",
            ),
            "execution_checkpoints": (
                "check(state in ('active', 'succeeded', 'failed', 'stopped'))",
                "check(elapsed_seconds >= 0)",
                "check((state = 'active' and finished_at is null and failure_code is null and failure_message is null and failure_recorded_at is null) or (state = 'succeeded' and started_at is not null and finished_at is not null and failure_code is null and failure_message is null and failure_recorded_at is null) or (state = 'failed' and started_at is not null and finished_at is not null and failure_code is not null and failure_message is not null and failure_recorded_at is not null) or (state = 'stopped' and finished_at is null and failure_code is not null and failure_message is not null and failure_recorded_at is not null))",
            ),
            "workers": (
                "check(pid >= 1)",
                "check(state in ('running', 'exited'))",
            ),
            "staged_executions": (
                "check(complete in (0, 1))",
                "check(retained_bytes >= 0)",
            ),
            "staged_diagnostics": ("check(position >= 0)",),
            "artifact_comparisons": (
                "check(available in (0, 1))",
                "check(outcome in ('matched', 'changed', 'failed', 'comparison_failed', 'skipped'))",
            ),
            "artifact_comparison_evidence": (
                "check(position >= 0)",
                "check(matched in (0, 1))",
            ),
            "publication_state": (
                "check(stage in ('not_ready', 'ready', 'publishing', 'result_committed', 'complete'))",
                "check(result_generation >= 1)",
                "check(report_generation >= 1)",
                "check((failure_code is null and failure_message is null and failure_recorded_at is null) or (failure_code is not null and failure_message is not null and failure_recorded_at is not null))",
                "check((stage = 'not_ready' and publication_identity is null and result_generation is null and report_generation is null) or (stage in ('ready', 'publishing') and publication_identity is not null and result_generation is null and report_generation is null) or (stage = 'result_committed' and publication_identity is not null and result_generation is not null and report_generation is null) or (stage = 'complete' and publication_identity is not null and result_generation is not null and report_generation is not null))",
            ),
        }
        named_indexes = {
            "accepted_commands_identity": (
                "accepted_commands",
                ("run_id", "entry", "execution_id"),
            ),
            "accepted_executions_order": (
                "accepted_executions",
                ("run_id", "plan_order"),
            ),
            "execution_checkpoints_state": (
                "execution_checkpoints",
                ("run_id", "state", "command_pk"),
            ),
            "workers_state": ("workers", ("run_id", "state", "command_pk")),
            "artifact_comparisons_outcome": (
                "artifact_comparisons",
                ("run_id", "outcome", "command_pk", "artifact"),
            ),
        }
        with _job_fixture() as (*_unused, run_root):
            database = run_root / "state.sqlite"
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(
                    db.execute("PRAGMA journal_mode").fetchone()[0], "delete"
                )
                self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 2)
                schemas = {
                    name: sql
                    for name, sql in db.execute(
                        "SELECT name, sql FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    )
                }
                self.assertEqual(set(schemas), set(expected))
                for table, fields in expected.items():
                    columns = db.execute(f'PRAGMA table_info("{table}")').fetchall()
                    self.assertEqual([row[1] for row in columns], fields.split())
                    self.assertEqual(
                        tuple(
                            row[1]
                            for row in sorted(columns, key=lambda item: item[5])
                            if row[5]
                        ),
                        primary_keys[table],
                        table,
                    )
                    foreign_rows = db.execute(
                        f'PRAGMA foreign_key_list("{table}")'
                    ).fetchall()
                    self.assertTrue(
                        all(
                            row[5] == "NO ACTION" and row[7] == "NONE"
                            for row in foreign_rows
                        ),
                        table,
                    )
                    grouped: dict[int, list[tuple]] = {}
                    for foreign in foreign_rows:
                        grouped.setdefault(foreign[0], []).append(foreign)
                    observed_foreign = {
                        (
                            rows[0][2],
                            tuple(
                                (row[3], row[4])
                                for row in sorted(rows, key=lambda item: item[1])
                            ),
                            rows[0][6],
                        )
                        for rows in grouped.values()
                    }
                    self.assertEqual(observed_foreign, foreign_keys[table], table)
                    self.assertEqual(
                        "WITHOUT ROWID" in schemas[table].upper(),
                        table in without_rowid,
                        table,
                    )
                    observed_unique = {
                        tuple(
                            item[2]
                            for item in db.execute(
                                f'PRAGMA index_info("{index[1]}")'
                            )
                        )
                        for index in db.execute(f'PRAGMA index_list("{table}")')
                        if index[2] and index[3] == "u"
                    }
                    self.assertEqual(
                        observed_unique, unique_keys.get(table, set()), table
                    )
                    self.assertEqual(
                        schemas[table].upper().count("CHECK("),
                        check_counts.get(table, 0),
                        table,
                    )
                    normalized_schema = " ".join(schemas[table].lower().split())
                    for fragment in check_fragments.get(table, ()):
                        self.assertIn(fragment, normalized_schema, table)
                observed_indexes = {
                    name: (
                        table,
                        tuple(
                            item[2]
                            for item in db.execute(f'PRAGMA index_info("{name}")')
                        ),
                    )
                    for name, table in db.execute(
                        "SELECT name, tbl_name FROM sqlite_master WHERE type='index' "
                        "AND name NOT LIKE 'sqlite_%'"
                    )
                }
                self.assertEqual(observed_indexes, named_indexes)
                self.assertIn(
                    "DEFERRABLE INITIALLY DEFERRED", schemas["workers"].upper()
                )
                combined = "\n".join(schemas.values()).lower()
                self.assertNotIn("attempt", combined)
                self.assertNotIn("lineage", combined)
                self.assertNotIn("accepted_plans", combined)
                for token in (
                    "check(status in ('complete', 'stopped', 'failed'))",
                    "check(state in ('active', 'succeeded', 'failed', 'stopped'))",
                    "check(stage in ('not_ready', 'ready', 'publishing', 'result_committed', 'complete'))",
                    "check(claim_kind in ('read', 'write', 'writable'))",
                ):
                    self.assertIn(token, combined.replace("\n", " "))


class ReproductionJobStorageTransactionTests(unittest.TestCase):
    def test_evidence_scoped_early_failure_keeps_definition_projection_null(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            fixture, entry, _output, _execution_id, plan = (
                _evidence_scoped_fixture(project)
            )
            assert isinstance(entry, EntryContext)
            assert isinstance(plan, ReproductionPlan)
            accepted_at = "2030-01-01T00:00:00Z"
            run_id = "reproduce-20300101t000000z-evidence-failure"
            logical = canonical_run_path(
                accepted_at, run_leaf(fixture.log_root.name, entry.id, run_id)
            ).as_posix()
            run_root = project / logical
            run_root.mkdir(parents=True)
            create_job(run_root, AcceptedJob(run_id, plan, accepted_at, logical))
            _start_and_finish(run_root, plan, 0)
            definition = plan.comparison_context["comparisons"][0]
            assert isinstance(definition, dict)
            comparison = _comparison(plan, 0, outcome="comparison_failed")
            comparison = replace(
                comparison,
                complete=False,
                artifacts=(
                    replace(
                        comparison.artifacts[0],
                        reason="evidence_context_changed",
                        accepted_definition=str(definition["definition_identity"]),
                        evidence_definition=None,
                    ),
                ),
            )
            record_execution_comparison(run_root, comparison)
            projection = load_publication_projection(run_root)
            self.assertIsNone(
                projection.comparisons[0].artifacts[0].evidence_definition
            )
            self.assertEqual(audit_job_state(run_root).integrity_check, "ok")

    def test_success_requires_exact_typed_output_inventory(self) -> None:
        with _job_fixture(executions=1, outputs_per_execution=2) as (
            *_unused,
            plan,
            _accepted,
            run_root,
        ):
            execution = plan.executions[0]
            entry = str(execution["entry"])
            execution_id = str(execution["execution_id"])
            record = plan.commands[0]["execution_state"]
            assert isinstance(record, dict)
            observed = record["observed"]
            assert isinstance(observed, dict)
            fingerprints = observed["outputs"]
            assert isinstance(fingerprints, dict)
            outputs = tuple(
                CheckpointOutput(artifact, fingerprint)
                for artifact, fingerprint in fingerprints.items()
            )
            attach_execution_permit(
                run_root,
                ExecutionPermitAttachment(
                    entry,
                    execution_id,
                    "permit-outputs",
                    "2030-01-01T00:00:01Z",
                ),
            )
            record_execution_start(
                run_root,
                ExecutionStart(
                    entry,
                    execution_id,
                    "permit-outputs",
                    "2030-01-01T00:00:01Z",
                    "2030-01-01T00:00:01Z",
                    "/private/tmp/reproduction-outputs",
                ),
            )
            terminal = ExecutionTerminal(
                entry,
                execution_id,
                "permit-outputs",
                "succeeded",
                "2030-01-01T00:00:02Z",
                "2030-01-01T00:00:02Z",
                1.0,
                outputs,
            )
            with self.assertRaises(JobStoreInvariantError):
                record_execution_terminal(
                    run_root, replace(terminal, outputs=outputs[:1])
                )
            with self.assertRaises(JobStoreInvariantError):
                record_execution_terminal(
                    run_root,
                    replace(
                        terminal,
                        outputs=(replace(outputs[0], fingerprint={}), outputs[1]),
                    ),
                )
            with self.assertRaises(JobStoreInvariantError):
                record_execution_terminal(
                    run_root, replace(terminal, elapsed_seconds=True)
                )
            record_execution_terminal(run_root, terminal)
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute(
                    "UPDATE checkpoint_outputs SET fingerprint_json='{}' "
                    "WHERE artifact=?",
                    (outputs[0].artifact,),
                )
            with self.assertRaises(JobStoreInvariantError):
                load_run_status(run_root)

    def test_worker_tree_and_owner_times_are_monotonic(self) -> None:
        with _job_fixture(executions=1) as (*_unused, plan, _accepted, run_root):
            execution = plan.executions[0]
            identity = ExecutionIdentity(
                str(execution["entry"]), str(execution["execution_id"])
            )
            attach_execution_permit(
                run_root,
                ExecutionPermitAttachment(
                    identity.entry,
                    identity.execution_id,
                    "permit-cycle",
                    "2030-01-01T00:00:01Z",
                ),
            )
            record_execution_start(
                run_root,
                ExecutionStart(
                    identity.entry,
                    identity.execution_id,
                    "permit-cycle",
                    "2030-01-01T00:00:01Z",
                    "2030-01-01T00:00:01Z",
                    "/private/tmp/reproduction-cycle",
                ),
            )
            with self.assertRaises(JobStoreInvariantError):
                replace_execution_workers(
                    run_root,
                    identity,
                    (
                        WorkerRecord(
                            "worker-a",
                            "worker-b",
                            4101,
                            "running",
                            "2030-01-01T00:00:01Z",
                            "2030-01-01T00:00:02Z",
                        ),
                        WorkerRecord(
                            "worker-b",
                            "worker-a",
                            4102,
                            "running",
                            "2030-01-01T00:00:01Z",
                            "2030-01-01T00:00:02Z",
                        ),
                    ),
                )
            with self.assertRaises(JobStoreInvariantError):
                replace_run_owner(
                    run_root,
                    RunOwner(
                        4100,
                        "running",
                        "2030-01-01T00:00:03Z",
                        "2030-01-01T00:00:02Z",
                    ),
                )

    def test_operational_failure_cleanup_derives_failed_terminal_state(self) -> None:
        with _job_fixture(executions=1) as (*_unused, plan, _accepted, run_root):
            execution = plan.executions[0]
            entry = str(execution["entry"])
            execution_id = str(execution["execution_id"])
            attach_execution_permit(
                run_root,
                ExecutionPermitAttachment(
                    entry,
                    execution_id,
                    "permit-failure",
                    "2030-01-01T00:00:01Z",
                ),
            )
            failure = RunFailure(
                "reproduction.execution.persistence_failed",
                "checkpoint persistence failed",
                "2030-01-01T00:00:03Z",
            )
            request_run_failure(run_root, failure)
            request_run_failure(run_root, failure)
            with self.assertRaises(JobStoreTransitionError):
                attach_execution_permit(
                    run_root,
                    ExecutionPermitAttachment(
                        entry,
                        execution_id,
                        "permit-failure",
                        "2030-01-01T00:00:03Z",
                    ),
                )
            with self.assertRaises(JobStoreTransitionError):
                request_run_failure(run_root, replace(failure, code="different_failure"))
            request_run_stop(run_root, RunStopRequest("2030-01-01T00:00:04Z"))
            self.assertIsNone(load_run_status(run_root).stop_requested_at)
            with self.assertRaises(JobStoreTransitionError):
                record_execution_start(
                    run_root,
                    ExecutionStart(
                        entry,
                        execution_id,
                        "permit-failure",
                        "2030-01-01T00:00:04Z",
                        "2030-01-01T00:00:04Z",
                        "/private/tmp/reproduction-failure",
                    ),
                )
            record_execution_terminal(
                run_root,
                ExecutionTerminal(
                    entry,
                    execution_id,
                    "permit-failure",
                    "stopped",
                    "2030-01-01T00:00:04Z",
                    None,
                    0.0,
                    failure_code="execution_stopped",
                    failure_message="failure cleanup",
                    failure_recorded_at="2030-01-01T00:00:04Z",
                ),
            )
            clear_execution_permit(
                run_root,
                ExecutionIdentity(entry, execution_id),
                "permit-failure",
                "stopped",
                "2030-01-01T00:00:04Z",
            )
            finish_run_stop(run_root, RunStopCompletion("2030-01-01T00:00:05Z"))
            status = load_run_status(run_root)
            self.assertEqual((status.status, status.phase), ("failed", None))
            self.assertEqual(status.finished_at, "2030-01-01T00:00:05Z")
            self.assertIsNone(status.stopped_at)
            self.assertEqual(
                status.operational_failure.code
                if status.operational_failure is not None
                else None,
                failure.code,
            )

    def test_later_success_preserves_latest_execution_diagnostic(self) -> None:
        with _job_fixture(executions=2) as (*_unused, plan, _accepted, run_root):
            _start_and_fail(run_root, plan, 0)
            failed = load_run_status(run_root).latest_execution_diagnostic
            self.assertIsNotNone(failed)
            _start_and_finish(run_root, plan, 1)
            self.assertEqual(load_run_status(run_root).latest_execution_diagnostic, failed)

    def test_stopping_run_rejects_comparison_and_publication_phase_changes(self) -> None:
        with _job_fixture(executions=1) as (*_unused, plan, _accepted, run_root):
            entry, execution_id, _baseline = _start_and_finish(run_root, plan, 0)
            comparison = _comparison(plan, 0)
            record_execution_comparison(run_root, comparison)
            record_requirement_effect(
                run_root,
                RequirementEffect(entry, execution_id, "2030-01-01T00:01:01Z"),
            )
            request_run_stop(run_root, RunStopRequest("2030-01-01T00:01:02Z"))
            before = load_publication_projection(run_root)
            with self.assertRaises(JobStoreTransitionError):
                record_execution_comparison(
                    run_root,
                    replace(comparison, recorded_at="2030-01-01T00:01:03Z"),
                )
            with self.assertRaises(JobStoreTransitionError):
                prepare_publication(
                    run_root, "f" * 64, updated_at="2030-01-01T00:01:03Z"
                )
            self.assertEqual(load_publication_projection(run_root), before)

    def test_publishing_phase_cannot_reopen_execution_or_comparison(self) -> None:
        with _job_fixture(executions=2) as (*_unused, plan, _accepted, run_root):
            _start_and_finish(run_root, plan, 0)
            pending = plan.executions[1]
            attachment = ExecutionPermitAttachment(
                str(pending["entry"]),
                str(pending["execution_id"]),
                "permit-publishing",
                "2030-01-01T00:00:03Z",
            )
            attach_execution_permit(run_root, attachment)
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute("UPDATE run_state SET phase='publishing'")
            with self.assertRaises(JobStoreTransitionError):
                attach_execution_permit(run_root, attachment)
            with self.assertRaises(JobStoreTransitionError):
                record_execution_start(
                    run_root,
                    ExecutionStart(
                        attachment.entry,
                        attachment.execution_id,
                        attachment.permit_id,
                        "2030-01-01T00:00:04Z",
                        "2030-01-01T00:00:04Z",
                        "/private/tmp/reproduction-publishing",
                    ),
                )
            with self.assertRaises(JobStoreTransitionError):
                record_execution_comparison(run_root, _comparison(plan, 0))
            status = load_run_status(run_root)
            self.assertEqual(status.phase, "publishing")
            self.assertIsNone(status.checkpoints[1].scratch_path)

    def test_permit_stop_cleanup_and_resume_use_identity_compare_and_set(self) -> None:
        with _job_fixture(executions=1) as (*_unused, plan, _accepted, run_root):
            execution = plan.executions[0]
            entry = str(execution["entry"])
            execution_id = str(execution["execution_id"])
            identity = ExecutionIdentity(entry, execution_id)
            attachment = ExecutionPermitAttachment(
                entry,
                execution_id,
                "permit-stop",
                "2030-01-01T00:00:01Z",
            )
            attach_execution_permit(run_root, attachment)
            attach_execution_permit(run_root, attachment)
            attached = load_run_status(run_root).checkpoints[0]
            self.assertIsNone(attached.started_at)
            with self.assertRaises(JobStoreTransitionError):
                attach_execution_permit(
                    run_root, replace(attachment, permit_id="permit-other")
                )
            record_execution_start(
                run_root,
                ExecutionStart(
                    entry,
                    execution_id,
                    "permit-stop",
                    "2030-01-01T00:00:02Z",
                    "2030-01-01T00:00:02Z",
                    "/private/tmp/reproduction-stop",
                ),
            )
            replace_execution_workers(
                run_root,
                identity,
                (
                    WorkerRecord(
                        "worker-stop",
                        None,
                        4321,
                        "running",
                        "2030-01-01T00:00:02Z",
                        "2030-01-01T00:00:03Z",
                    ),
                ),
            )
            worker_before = load_run_status(run_root)
            with self.assertRaises(JobStoreTransitionError):
                replace_execution_workers(run_root, identity, ())
            with self.assertRaises(JobStoreTransitionError):
                replace_execution_workers(
                    run_root,
                    identity,
                    (
                        WorkerRecord(
                            "worker-stop",
                            None,
                            9999,
                            "running",
                            "2030-01-01T00:00:02Z",
                            "2030-01-01T00:00:03Z",
                        ),
                    ),
                )
            self.assertEqual(load_run_status(run_root), worker_before)
            replace_execution_workers(
                run_root,
                identity,
                (
                    WorkerRecord(
                        "worker-stop",
                        None,
                        4321,
                        "exited",
                        "2030-01-01T00:00:02Z",
                        "2030-01-01T00:00:03Z",
                    ),
                ),
            )
            with self.assertRaises(JobStoreTransitionError):
                replace_execution_workers(
                    run_root,
                    identity,
                    (
                        WorkerRecord(
                            "worker-stop",
                            None,
                            4321,
                            "running",
                            "2030-01-01T00:00:02Z",
                            "2030-01-01T00:00:04Z",
                        ),
                    ),
                )
            request_run_stop(
                run_root, RunStopRequest("2030-01-01T00:00:04Z")
            )
            request_run_stop(
                run_root, RunStopRequest("2030-01-01T00:00:05Z")
            )
            self.assertEqual(
                load_run_status(run_root).stop_requested_at,
                "2030-01-01T00:00:04Z",
            )
            record_execution_terminal(
                run_root,
                ExecutionTerminal(
                    entry,
                    execution_id,
                    "permit-stop",
                    "stopped",
                    "2030-01-01T00:00:06Z",
                    None,
                    4.0,
                    failure_code="execution_stopped",
                    failure_message="stopped by fixture",
                    failure_recorded_at="2030-01-01T00:00:06Z",
                    workers=(
                        WorkerRecord(
                            "worker-stop",
                            None,
                            4321,
                            "exited",
                            "2030-01-01T00:00:02Z",
                            "2030-01-01T00:00:06Z",
                        ),
                    ),
                ),
            )
            with self.assertRaises(JobStoreTransitionError):
                clear_execution_permit(
                    run_root,
                    identity,
                    "permit-other",
                    "stopped",
                    "2030-01-01T00:00:06Z",
                )
            clear_execution_permit(
                run_root,
                identity,
                "permit-stop",
                "stopped",
                "2030-01-01T00:00:06Z",
            )
            cleared = load_run_status(run_root).checkpoints[0]
            self.assertIsNone(cleared.permit_id)
            self.assertEqual(cleared.released_permit_id, "permit-stop")
            with self.assertRaises(JobStoreTransitionError):
                clear_execution_permit(
                    run_root,
                    identity,
                    "permit-other",
                    "stopped",
                    "2030-01-01T00:00:06Z",
                )
            clear_execution_permit(
                run_root,
                identity,
                "permit-stop",
                "stopped",
                "2030-01-01T00:00:06Z",
            )
            clear_execution_scratch(
                run_root, identity, "/private/tmp/reproduction-stop"
            )
            clear_execution_scratch(
                run_root, identity, "/private/tmp/reproduction-stop"
            )
            finish_run_stop(
                run_root,
                RunStopCompletion("2030-01-01T00:00:07Z"),
            )
            stopped = load_run_status(run_root)
            self.assertEqual(stopped.status, "stopped")
            self.assertIsNone(stopped.checkpoints[0].finished_at)
            self.assertEqual(stopped.completed_executions, 0)
            begin_run_resume(
                run_root,
                RunResumeRequest("2030-01-01T00:00:08Z"),
            )
            resumed = load_run_status(run_root)
            self.assertEqual((resumed.status, resumed.phase), (None, "accepted"))
            self.assertIsNone(resumed.stopped_at)
            attach_execution_permit(
                run_root,
                ExecutionPermitAttachment(
                    entry,
                    execution_id,
                    "permit-resume",
                    "2030-01-01T00:00:09Z",
                    expected_state="stopped",
                ),
            )
            relaunched = load_run_status(run_root).checkpoints[0]
            self.assertEqual(relaunched.started_at, "2030-01-01T00:00:02Z")
            self.assertEqual(relaunched.elapsed_seconds, 4.0)
            before_delayed_terminal = load_run_status(run_root)
            with self.assertRaises(JobStoreTransitionError):
                record_execution_terminal(
                    run_root,
                    ExecutionTerminal(
                        entry,
                        execution_id,
                        "permit-stop",
                        "stopped",
                        "2030-01-01T00:00:10Z",
                        None,
                        4.0,
                        failure_code="execution_stopped",
                        failure_message="delayed old terminal",
                        failure_recorded_at="2030-01-01T00:00:10Z",
                    ),
                )
            self.assertEqual(load_run_status(run_root), before_delayed_terminal)
            with self.assertRaises(JobStoreTransitionError):
                record_execution_terminal(
                    run_root,
                    ExecutionTerminal(
                        entry,
                        execution_id,
                        "permit-resume",
                        "succeeded",
                        "2030-01-01T00:00:10Z",
                        "2030-01-01T00:00:10Z",
                        4.0,
                    ),
                )
            record_execution_start(
                run_root,
                ExecutionStart(
                    entry,
                    execution_id,
                    "permit-resume",
                    "2030-01-01T00:00:10Z",
                    "2030-01-01T00:00:10Z",
                    "/private/tmp/reproduction-resume",
                    elapsed_seconds=4.0,
                ),
            )
            for elapsed, finished_at in (
                (3.0, "2030-01-01T00:00:11Z"),
                (5.0, "2030-01-01T00:00:01Z"),
            ):
                with self.assertRaises(JobStoreTransitionError):
                    record_execution_terminal(
                        run_root,
                        ExecutionTerminal(
                            entry,
                            execution_id,
                            "permit-resume",
                            "succeeded",
                            "2030-01-01T00:00:11Z",
                            finished_at,
                            elapsed,
                        ),
                    )

    def test_acceptance_and_terminal_interruptions_roll_back(self) -> None:
        with _job_fixture(create=False) as (*_unused, accepted, run_root):
            with mock.patch.object(
                job_storage,
                "_before_commit",
                side_effect=RuntimeError("interrupted acceptance"),
            ):
                with self.assertRaisesRegex(RuntimeError, "interrupted acceptance"):
                    create_job(run_root, accepted)
            self.assertFalse((run_root / "state.sqlite").exists())
            create_job(run_root, accepted)
            plan = accepted.plan
            execution = plan.executions[0]
            entry = str(execution["entry"])
            execution_id = str(execution["execution_id"])
            attach_execution_permit(
                run_root,
                ExecutionPermitAttachment(
                    entry,
                    execution_id,
                    "permit-interrupted",
                    "2030-01-01T00:00:01Z",
                ),
            )
            record_execution_start(
                run_root,
                ExecutionStart(
                    entry,
                    execution_id,
                    "permit-interrupted",
                    "2030-01-01T00:00:01Z",
                    "2030-01-01T00:00:01Z",
                    "/private/tmp/reproduction-interrupted",
                ),
            )
            before = load_run_status(run_root)
            with mock.patch.object(
                job_storage,
                "_before_commit",
                side_effect=lambda operation, _db: (
                    (_ for _ in ()).throw(RuntimeError("interrupted terminal"))
                    if operation == "terminal_checkpoint"
                    else None
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "interrupted terminal"):
                    record_execution_terminal(
                        run_root,
                        ExecutionTerminal(
                            entry,
                            execution_id,
                            "permit-interrupted",
                            "succeeded",
                            "2030-01-01T00:00:02Z",
                            "2030-01-01T00:00:02Z",
                            1.0,
                            (
                                CheckpointOutput(
                                    _comparison(plan, 0).artifacts[0].artifact,
                                    _comparison(plan, 0).artifacts[0].accepted_baseline,
                                ),
                            ),
                        ),
                    )
            self.assertEqual(load_run_status(run_root), before)

    def test_comparison_effect_and_publication_interruptions_roll_back(self) -> None:
        with _job_fixture(executions=1) as (*_unused, plan, _accepted, run_root):
            entry, execution_id, _baseline = _start_and_finish(run_root, plan, 0)
            first = _comparison(plan, 0)
            record_execution_comparison(run_root, first)
            before = load_publication_projection(run_root)
            replacement = replace(
                first,
                artifacts=(
                    replace(
                        first.artifacts[0], outcome="changed", reason="content_changed"
                    ),
                ),
                recorded_at="2030-01-01T00:01:01Z",
            )
            with mock.patch.object(
                job_storage,
                "_before_commit",
                side_effect=lambda operation, _db: (
                    (_ for _ in ()).throw(RuntimeError("interrupted comparison"))
                    if operation == "comparison"
                    else None
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "interrupted comparison"):
                    record_execution_comparison(run_root, replacement)
            self.assertEqual(load_publication_projection(run_root), before)

            effect = RequirementEffect(entry, execution_id, "2030-01-01T00:01:02Z")
            with mock.patch.object(
                job_storage,
                "_before_commit",
                side_effect=lambda operation, _db: (
                    (_ for _ in ()).throw(RuntimeError("interrupted effect"))
                    if operation == "requirement_effect"
                    else None
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "interrupted effect"):
                    record_requirement_effect(run_root, effect)
            with sqlite3.connect(run_root / "state.sqlite") as db:
                self.assertIsNone(
                    db.execute(
                        "SELECT requirement_cleared_at FROM execution_effects"
                    ).fetchone()[0]
                )
            record_requirement_effect(run_root, effect)

            publication_identity = "a" * 64
            before_publication = load_publication_projection(run_root).publication
            with mock.patch.object(
                job_storage,
                "_before_commit",
                side_effect=lambda operation, _db: (
                    (_ for _ in ()).throw(RuntimeError("interrupted publication"))
                    if operation == "publication_state"
                    else None
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "interrupted publication"):
                    prepare_publication(
                        run_root,
                        publication_identity,
                        updated_at="2030-01-01T00:01:03Z",
                    )
            self.assertEqual(
                load_publication_projection(run_root).publication,
                before_publication,
            )
            prepare_publication(
                run_root,
                publication_identity,
                updated_at="2030-01-01T00:01:04Z",
            )
            begin_publication(
                run_root,
                publication_identity,
                updated_at="2030-01-01T00:01:05Z",
            )
            record_publication_failure(
                run_root,
                "publishing",
                PublicationFailure(
                    "results.write_failed",
                    "result write failed",
                    "2030-01-01T00:01:06Z",
                ),
            )
            retry = load_publication_projection(run_root)
            self.assertEqual(retry.publication.stage, "ready")
            self.assertEqual(retry.status, "failed")
            begin_publication_resume(
                run_root,
                PublicationResumeRequest(
                    publication_identity,
                    "ready",
                    "2030-01-01T00:01:07Z",
                ),
            )
            self.assertEqual(load_run_status(run_root).phase, "publishing")

    def test_dependency_failure_skip_is_durable_terminal_accounting(self) -> None:
        with _job_fixture(executions=2, create=False) as (
            *_unused,
            plan,
            accepted,
            run_root,
        ):
            executions = list(plan.executions)
            executions[1] = dict(
                executions[1],
                depends_on=[
                    f"{executions[0]['entry']}:{executions[0]['execution_id']}"
                ],
            )
            plan = replace(plan, executions=tuple(executions))
            create_job(run_root, replace(accepted, plan=plan))
            _start_and_fail(run_root, plan, 0)
            failed = _comparison(plan, 0, outcome="failed")
            failed = replace(
                failed,
                complete=False,
                retained_bytes=0,
                artifacts=(
                    replace(
                        failed.artifacts[0],
                        available=False,
                        staged_path=None,
                        reason="execution_failed",
                        profile=None,
                        regenerated=None,
                    ),
                ),
            )
            with self.assertRaises(JobStoreInvariantError):
                record_execution_comparison(
                    run_root,
                    replace(
                        failed,
                        artifacts=(
                            replace(
                                failed.artifacts[0],
                                evidence_definition="d" * 64,
                            ),
                        ),
                    ),
                )
            record_execution_comparison(run_root, failed)
            skipped = _comparison(plan, 1, outcome="skipped")
            skipped = replace(
                skipped,
                complete=False,
                retained_bytes=0,
                artifacts=(
                    replace(
                        skipped.artifacts[0],
                        available=False,
                        staged_path=None,
                        reason="dependency_failed",
                        profile=None,
                        regenerated=None,
                    ),
                ),
            )
            with self.assertRaises(JobStoreTransitionError):
                record_execution_comparison(
                    run_root, replace(skipped, artifacts=())
                )
            record_execution_comparison(run_root, skipped)
            status = load_run_status(run_root)
            self.assertEqual(status.completed_executions, 2)
            self.assertEqual(status.artifact_outcomes["skipped"], 1)
            prepare_publication(
                run_root, "e" * 64, updated_at="2030-01-01T00:02:00Z"
            )
            self.assertEqual(audit_job_state(run_root).integrity_check, "ok")

    def test_owner_workers_and_publication_compare_and_set(self) -> None:
        with _job_fixture(executions=1) as (*_unused, plan, _accepted, run_root):
            entry, execution_id, _baseline = _start_and_finish(run_root, plan, 0)
            replace_run_owner(
                run_root,
                RunOwner(
                    1234,
                    "running",
                    "2030-01-01T00:00:01Z",
                    "2030-01-01T00:00:02Z",
                ),
            )
            owner = load_scheduler_owner(run_root)
            self.assertEqual(owner.owner.supervisor_pid if owner.owner else None, 1234)
            self.assertEqual(owner.running_workers, ())
            record_execution_comparison(run_root, _comparison(plan, 0))
            record_requirement_effect(
                run_root,
                RequirementEffect(entry, execution_id, "2030-01-01T00:01:01Z"),
            )
            identity = "b" * 64
            prepare_publication(run_root, identity, updated_at="2030-01-01T00:01:02Z")
            begin_publication(run_root, identity, updated_at="2030-01-01T00:01:03Z")
            record_result_commit(
                run_root, identity, 7, updated_at="2030-01-01T00:01:04Z"
            )
            record_publication_failure(
                run_root,
                "result_committed",
                PublicationFailure(
                    "results.report.write_failed",
                    "report write failed",
                    "2030-01-01T00:01:05Z",
                ),
            )
            self.assertEqual(
                load_publication_projection(run_root).publication.stage,
                "result_committed",
            )
            failed = load_run_status(run_root)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(
                failed.operational_failure.code
                if failed.operational_failure is not None
                else None,
                "reproduction.publication.failed",
            )
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute(
                    "UPDATE run_state SET operational_message='mismatched failure'"
                )
            with self.assertRaises(JobStoreTransitionError):
                begin_publication_resume(
                    run_root,
                    PublicationResumeRequest(
                        identity,
                        "result_committed",
                        "2030-01-01T00:01:06Z",
                    ),
                )
            with self.assertRaises(JobStoreInvariantError):
                audit_job_state(run_root)
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute(
                    "UPDATE run_state SET operational_message='report write failed'"
                )
            with self.assertRaises(JobStoreTransitionError):
                begin_publication_resume(
                    run_root,
                    PublicationResumeRequest(
                        "c" * 64,
                        "result_committed",
                        "2030-01-01T00:01:06Z",
                    ),
                )
            begin_publication_resume(
                run_root,
                PublicationResumeRequest(
                    identity,
                    "result_committed",
                    "2030-01-01T00:01:06Z",
                ),
            )
            self.assertEqual(
                (load_run_status(run_root).status, load_run_status(run_root).phase),
                (None, "publishing"),
            )
            record_report_commit(
                run_root, identity, 7, updated_at="2030-01-01T00:01:07Z"
            )
            projection = load_publication_projection(run_root)
            self.assertEqual(projection.publication.stage, "complete")
            self.assertEqual(projection.status, "complete")
            with self.assertRaises(JobStoreTransitionError):
                record_report_commit(
                    run_root, identity, 7, updated_at="2030-01-01T00:01:07Z"
                )

    def test_scheduler_owner_reads_only_live_identity_and_worker_tree(self) -> None:
        with _job_fixture(executions=2) as (*_unused, plan, _accepted, run_root):
            _start_and_finish(run_root, plan, 0)
            execution = plan.executions[1]
            identity = ExecutionIdentity(
                str(execution["entry"]), str(execution["execution_id"])
            )
            attach_execution_permit(
                run_root,
                ExecutionPermitAttachment(
                    identity.entry,
                    identity.execution_id,
                    "permit-live",
                    "2030-01-01T00:00:03Z",
                ),
            )
            record_execution_start(
                run_root,
                ExecutionStart(
                    identity.entry,
                    identity.execution_id,
                    "permit-live",
                    "2030-01-01T00:00:03Z",
                    "2030-01-01T00:00:03Z",
                    "/private/tmp/reproduction-live",
                ),
            )
            replace_execution_workers(
                run_root,
                identity,
                (
                    WorkerRecord(
                        "z-parent",
                        None,
                        3001,
                        "running",
                        "2030-01-01T00:00:03Z",
                        "2030-01-01T00:00:04Z",
                    ),
                    WorkerRecord(
                        "a-child",
                        "z-parent",
                        3002,
                        "running",
                        "2030-01-01T00:00:03Z",
                        "2030-01-01T00:00:04Z",
                    ),
                ),
            )
            replace_run_owner(
                run_root,
                RunOwner(
                    3000,
                    "running",
                    "2030-01-01T00:00:03Z",
                    "2030-01-01T00:00:04Z",
                ),
            )
            status_keys = [
                (
                    identity.entry if identity is not None else "",
                    identity.execution_id if identity is not None else "",
                    worker.worker_id,
                )
                for identity, worker in load_run_status(run_root).workers
            ]
            self.assertEqual(status_keys, sorted(status_keys))
            traces: list[str] = []
            with open_locked_job(run_root, trace=traces.append) as store:
                owner = store.load_scheduler_owner()
            self.assertEqual(len(owner.checkpoints), 1)
            self.assertEqual(
                [worker.worker_id for _identity, worker in owner.running_workers],
                ["a-child", "z-parent"],
            )
            self.assertTrue(
                any(
                    "permit_id is not null" in statement.lower()
                    for statement in traces
                )
            )
            self.assertFalse(
                any("select r.*, s.*" in statement.lower() for statement in traces)
            )
            output_reads = [
                statement.lower()
                for statement in traces
                if "from checkpoint_outputs" in statement.lower()
            ]
            self.assertEqual(output_reads, [])
            self.assertEqual(audit_job_state(run_root).integrity_check, "ok")
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute("DELETE FROM run_owner")
            with self.assertRaises(JobStoreInvariantError):
                load_scheduler_owner(run_root)


class ReproductionJobStorageIsolationTests(unittest.TestCase):
    def test_production_reads_are_identity_local_and_bounded(self) -> None:
        with _job_fixture(executions=2) as (*_unused, plan, _accepted, run_root):
            entry, execution_id, _baseline = _start_and_finish(run_root, plan, 0)
            record_execution_comparison(run_root, _comparison(plan, 0))
            identity = ExecutionIdentity(entry, execution_id)
            checkpoint = load_execution_checkpoint(run_root, identity)
            control = load_run_control(run_root)
            effect = load_requirement_effect(run_root, identity)
            self.assertIsNotNone(checkpoint)
            self.assertEqual(control.phase, "comparing")
            self.assertIsNotNone(effect.comparison_recorded_at)
            traces: list[str] = []
            with open_locked_job(run_root, trace=traces.append) as store:
                self.assertEqual(
                    store.load_execution_checkpoint(identity), checkpoint
                )
                self.assertEqual(store.load_run_control(), control)
                self.assertEqual(store.load_requirement_effect(identity), effect)
            statements = "\n".join(traces).lower()
            self.assertNotIn("from staged_executions", statements)
            self.assertNotIn("from accepted_materials", statements)
            self.assertNotIn("order by e.plan_order", statements)
            self.assertIn("where p.run_id=", statements)

    def test_one_checkpoint_update_does_not_read_or_write_siblings(self) -> None:
        with _job_fixture(executions=8) as (*_unused, plan, _accepted, run_root):
            for index, execution in enumerate(plan.executions):
                attach_execution_permit(
                    run_root,
                    ExecutionPermitAttachment(
                        str(execution["entry"]),
                        str(execution["execution_id"]),
                        f"permit-{index}",
                        f"2030-01-01T00:00:{index + 1:02d}Z",
                    ),
                )
                record_execution_start(
                    run_root,
                    ExecutionStart(
                        str(execution["entry"]),
                        str(execution["execution_id"]),
                        f"permit-{index}",
                        f"2030-01-01T00:00:{index + 1:02d}Z",
                        f"2030-01-01T00:00:{index + 1:02d}Z",
                        f"/private/tmp/reproduction-locality-{index}",
                    ),
                )
            database = run_root / "state.sqlite"
            with sqlite3.connect(database) as db:
                before = db.execute(
                    "SELECT command_pk, state, checkpointed_at, elapsed_seconds "
                    "FROM execution_checkpoints ORDER BY command_pk"
                ).fetchall()
            target = plan.executions[3]
            traces: list[str] = []
            writes: list[tuple[str, str]] = []
            with open_locked_job(
                run_root,
                trace=traces.append,
                update_hook=lambda operation, table: writes.append((operation, table)),
            ) as store:
                store.record_execution_terminal(
                    ExecutionTerminal(
                        str(target["entry"]),
                        str(target["execution_id"]),
                        "permit-3",
                        "failed",
                        "2030-01-01T00:01:00Z",
                        "2030-01-01T00:01:00Z",
                        4.0,
                        failure_code="execution_failed",
                        failure_message="fixture failure",
                        failure_recorded_at="2030-01-01T00:01:00Z",
                    )
                )
            with sqlite3.connect(database) as db:
                after = db.execute(
                    "SELECT command_pk, state, checkpointed_at, elapsed_seconds "
                    "FROM execution_checkpoints ORDER BY command_pk"
                ).fetchall()
            self.assertEqual(
                [row for row in after if row[0] != 4],
                [row for row in before if row[0] != 4],
            )
            self.assertEqual([row for row in after if row[0] == 4][0][1], "failed")
            self.assertEqual(
                {table for _operation, table in writes},
                {
                    "checkpoint_outputs",
                    "execution_checkpoints",
                    "run_state",
                    "workers",
                },
            )
            self.assertFalse(
                any(
                    "select * from execution_checkpoints" in item.lower()
                    for item in traces
                )
            )

    def test_one_comparison_update_writes_no_sibling_or_plan_rows(self) -> None:
        with _job_fixture(executions=8) as (*_unused, plan, _accepted, run_root):
            for index in range(8):
                _start_and_finish(run_root, plan, index)
                record_execution_comparison(run_root, _comparison(plan, index))
            database = run_root / "state.sqlite"
            with sqlite3.connect(database) as db:
                before = db.execute(
                    "SELECT command_pk, artifact, outcome, expected_json, regenerated_json "
                    "FROM artifact_comparisons ORDER BY command_pk"
                ).fetchall()
                plan_before = {
                    table: db.execute(f'SELECT * FROM "{table}"').fetchall()
                    for table in (
                        "accepted_commands",
                        "accepted_executions",
                        "accepted_materials",
                    )
                }
            traces: list[str] = []
            writes: list[tuple[str, str]] = []
            changed = _comparison(
                plan,
                3,
                outcome="changed",
                recorded_at="2030-01-01T00:02:00Z",
            )
            with open_locked_job(
                run_root,
                trace=traces.append,
                update_hook=lambda operation, table: writes.append((operation, table)),
            ) as store:
                store.record_execution_comparison(changed)
            with sqlite3.connect(database) as db:
                after = db.execute(
                    "SELECT command_pk, artifact, outcome, expected_json, regenerated_json "
                    "FROM artifact_comparisons ORDER BY command_pk"
                ).fetchall()
                plan_after = {
                    table: db.execute(f'SELECT * FROM "{table}"').fetchall()
                    for table in plan_before
                }
            target_pk = 4
            self.assertEqual(
                [row for row in after if row[0] != target_pk],
                [row for row in before if row[0] != target_pk],
            )
            self.assertEqual(plan_after, plan_before)
            self.assertFalse(
                any(table.startswith("accepted_") for _operation, table in writes)
            )
            self.assertFalse(
                any(
                    "select * from accepted_" in statement.lower()
                    for statement in traces
                )
            )
            changed_rows = [row for row in after if row[0] == target_pk]
            self.assertEqual(changed_rows[0][2], "changed")

    def test_disposable_cache_deletion_preserves_plan_and_audit(self) -> None:
        with _job_fixture() as (project, fixture, _entry, plan, _accepted, run_root):
            cache_paths = (
                fixture.log_root / ".cache" / "results.sqlite",
                project / ".cache" / "research-log-fingerprints.sqlite3",
                project / ".cache" / "research-log-validation.sqlite3",
            )
            for path in cache_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch(exist_ok=True)
            before = (run_root / "state.sqlite").read_bytes()
            for path in cache_paths:
                path.unlink()
            self.assertEqual(
                load_accepted_plan(run_root).serialized(), plan.serialized()
            )
            self.assertEqual((run_root / "state.sqlite").read_bytes(), before)
            audit = audit_job_state(run_root)
            self.assertEqual(audit.integrity_check, "ok")
            self.assertEqual(audit.foreign_key_violations, 0)

    def test_bounded_status_ignores_unrelated_corruption_but_audit_finds_it(
        self,
    ) -> None:
        with _job_fixture() as (*_unused, run_root):
            database = run_root / "state.sqlite"
            with sqlite3.connect(database) as db:
                db.execute("PRAGMA foreign_keys=OFF")
                db.execute(
                    "INSERT INTO artifact_comparison_evidence VALUES "
                    "(?, 999, 'missing', 0, 'record', '{}', '{}', '{}', 1)",
                    ("reproduce-20300101t000000z-foundation",),
                )
            self.assertEqual(load_run_status(run_root).phase, "accepted")
            with self.assertRaises(JobStoreInvariantError):
                audit_job_state(run_root)


class ReproductionJobStorageErrorTests(unittest.TestCase):
    def test_open_rejects_a_copied_store_at_a_different_run_root(self) -> None:
        with _job_fixture() as (project, *_unused, run_root):
            copied_root = (
                project
                / "tmp"
                / "reproduction"
                / "2030-01-01"
                / "reproduce-copied-reproduce-20300101t000000z-copied"
            )
            copied_root.mkdir(parents=True)
            shutil.copy2(
                run_root / "state.sqlite", copied_root / "state.sqlite"
            )
            with self.assertRaises(JobStoreInvariantError):
                load_run_status(copied_root)

    def test_audit_rejects_owner_and_publication_cross_row_corruption(self) -> None:
        with _job_fixture() as (*_unused, run_root):
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute(
                    "INSERT INTO run_owner VALUES (?, 5000, 'running', ?, ?)",
                    (
                        "reproduce-20300101t000000z-foundation",
                        "2030-01-01T00:00:01Z",
                        "2030-01-01T00:00:02Z",
                    ),
                )
                db.execute(
                    "UPDATE run_state SET status='stopped', phase=NULL, stopped_at=?",
                    ("2030-01-01T00:00:03Z",),
                )
            with self.assertRaises(JobStoreInvariantError):
                audit_job_state(run_root)
        with _job_fixture() as (*_unused, run_root):
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute(
                    "UPDATE publication_state SET failure_code='bad', "
                    "failure_message='bad state', failure_recorded_at=?",
                    ("2030-01-01T00:00:03Z",),
                )
            with self.assertRaises(JobStoreInvariantError):
                audit_job_state(run_root)
        with _job_fixture() as (*_unused, run_root):
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute(
                    "UPDATE run_state SET status='stopped', phase=NULL, stopped_at=?",
                    ("2030-01-01T00:00:03Z",),
                )
                db.execute(
                    "UPDATE publication_state SET stage='ready', "
                    "publication_identity=?",
                    ("f" * 64,),
                )
            with self.assertRaises(JobStoreInvariantError):
                audit_job_state(run_root)

    def test_acceptance_binds_date_and_exact_run_leaf(self) -> None:
        with _job_fixture(create=False) as (*_unused, accepted, run_root):
            with self.assertRaises(JobStoreInvariantError):
                create_job(
                    run_root,
                    replace(accepted, accepted_at="2030-01-02T00:00:00Z"),
                )
            with self.assertRaises(JobStoreInvariantError):
                create_job(
                    run_root,
                    replace(accepted, run_id=accepted.run_id.removesuffix("n")),
                )
            self.assertFalse((run_root / "state.sqlite").exists())

    def test_status_rejects_selected_lifecycle_and_identity_corruption(self) -> None:
        job_storage._require_execution_identity(
            "e1000", "pyrun-exec/v1:" + "a" * 64
        )
        with _job_fixture() as (*_unused, run_root):
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute(
                    "UPDATE run_state SET stop_requested_at=?",
                    ("2030-01-01T00:00:01Z",),
                )
            with self.assertRaises(JobStoreInvariantError):
                load_run_status(run_root)
        with _job_fixture() as (*_unused, run_root):
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute(
                    "UPDATE runs SET target_kind='entry', target_entry='e12'"
                )
            with self.assertRaises(JobStoreInvariantError):
                load_run_status(run_root)
        with _job_fixture() as (*_unused, plan, _accepted, run_root):
            execution = plan.executions[0]
            attach_execution_permit(
                run_root,
                ExecutionPermitAttachment(
                    str(execution["entry"]),
                    str(execution["execution_id"]),
                    "permit-corrupt",
                    "2030-01-01T00:00:01Z",
                ),
            )
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute("UPDATE execution_checkpoints SET permit_id=NULL")
            with self.assertRaises(JobStoreInvariantError):
                load_run_status(run_root)
        with _job_fixture() as (*_unused, plan, _accepted, run_root):
            execution = plan.executions[0]
            entry = str(execution["entry"])
            execution_id = str(execution["execution_id"])
            attach_execution_permit(
                run_root,
                ExecutionPermitAttachment(
                    entry,
                    execution_id,
                    "permit-terminal-corrupt",
                    "2030-01-01T00:00:01Z",
                ),
            )
            record_execution_terminal(
                run_root,
                ExecutionTerminal(
                    entry,
                    execution_id,
                    "permit-terminal-corrupt",
                    "stopped",
                    "2030-01-01T00:00:02Z",
                    None,
                    0.0,
                    failure_code="execution_stopped",
                    failure_message="fixture stop",
                    failure_recorded_at="2030-01-01T00:00:02Z",
                ),
            )
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute(
                    "UPDATE run_state SET status='stopped', phase=NULL, stopped_at=?",
                    ("2030-01-01T00:00:03Z",),
                )
            with self.assertRaises(JobStoreInvariantError):
                load_run_status(run_root)
            with self.assertRaises(JobStoreInvariantError):
                audit_job_state(run_root)
        with _job_fixture() as (*_unused, run_root):
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute(
                    "UPDATE run_state SET status='failed', phase=NULL, finished_at=?",
                    ("2030-01-01T00:00:03Z",),
                )
            with self.assertRaises(JobStoreInvariantError):
                load_run_status(run_root)

    def test_named_storage_error_classes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing"
            missing.mkdir()
            with self.assertRaises(JobStoreMissingError):
                load_run_status(missing)

            unsupported = root / "unsupported"
            unsupported.mkdir()
            with sqlite3.connect(unsupported / "state.sqlite") as db:
                db.execute("PRAGMA user_version=2")
            with self.assertRaises(JobStoreUnsupportedError):
                load_run_status(unsupported)

            malformed = root / "malformed"
            malformed.mkdir()
            (malformed / "state.sqlite").write_bytes(b"not sqlite")
            with self.assertRaises(JobStoreMalformedError):
                load_run_status(malformed)

            symlinked = root / "symlinked"
            symlinked.mkdir()
            os.symlink(unsupported / "state.sqlite", symlinked / "state.sqlite")
            with self.assertRaises(JobStoreSymlinkError):
                load_run_status(symlinked)

        with _job_fixture() as (*_unused, plan, _accepted, run_root):
            execution = plan.executions[0]
            with self.assertRaises(JobStoreTransitionError):
                record_execution_terminal(
                    run_root,
                    ExecutionTerminal(
                        str(execution["entry"]),
                        str(execution["execution_id"]),
                        "missing-permit",
                        "succeeded",
                        "2030-01-01T00:00:02Z",
                        "2030-01-01T00:00:02Z",
                        1.0,
                    ),
                )
            with self.assertRaises(JobStoreInvariantError):
                prepare_publication(
                    run_root,
                    "not-a-digest",
                    updated_at="2030-01-01T00:00:03Z",
                )
            lock = (run_root / "state.lock").open("r+b")
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(JobStoreBusyError):
                    load_run_status(run_root)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                lock.close()

    def test_selected_projection_rejects_malformed_leaf(self) -> None:
        with _job_fixture() as (*_unused, run_root):
            with sqlite3.connect(run_root / "state.sqlite") as db:
                db.execute(
                    "UPDATE accepted_commands SET details_json='not json' "
                    "WHERE command_pk=1"
                )
            with self.assertRaises(JobStoreInvariantError):
                load_accepted_plan(run_root)


if __name__ == "__main__":
    unittest.main()
