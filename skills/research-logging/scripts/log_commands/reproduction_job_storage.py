"""Durable per-run SQLite authority for fixed-plan reproduction jobs.

The store at ``<run>/state.sqlite`` owns one immutable accepted plan and the
small mutable rows used to advance that run.  It deliberately exposes typed
operations rather than a connection and is independent of disposable result
and fingerprint stores.
"""
# ruff: noqa: E501

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, Literal, Mapping, NoReturn, Sequence, cast

from research_log_data import DataContractError, parse_fingerprint
from validation.pyrun_outputs import code_target_path, output_target_path
from validation.pyrun_state import script_target_path

from .context import ENTRY_ID_RE
from .reproduction_contract import ReproductionPlan
from .reproduction_paths import (
    is_canonical_run_path,
    job_state_path,
    project_tmp_relative,
)

JOB_STORE_VERSION = 2
MAX_JOB_STORE_BYTES = 256 * 1024 * 1024
MAX_STATUS_BYTES = 64 * 1024 * 1024
MAX_CHECKPOINT_OUTPUTS = 256
MAX_EXECUTION_DEPENDENCIES = 2_048
MAX_ACCEPTED_SCHEDULING_CLAIMS = 4_096
MAX_EXECUTION_WORKERS = 1_024
MAX_RUN_WORKERS = 4_096
MAX_STRING_BYTES = 8 * 1024
MAX_PATH_BYTES = 2 * 1024
JOB_LOCK_NAME = "state.lock"
_COMPANIONS = ("-journal", "-wal", "-shm")
_RUN_ID_RE = re.compile(r"reproduce-[a-z0-9][a-z0-9-]{0,127}\Z")
_EXECUTION_ID_RE = re.compile(r"pyrun-exec/v2:[0-9a-f]{64}\Z")
_CID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_SOURCE_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_CHECKPOINT_STATES = frozenset({"active", "succeeded", "failed", "stopped"})
_TERMINAL_CHECKPOINT_STATES = frozenset({"succeeded", "failed", "stopped"})
_TERMINAL_RUN_STATUSES = frozenset({"complete", "stopped", "failed"})
_RUN_PHASES = frozenset(
    {
        "accepted",
        "planning",
        "preflight",
        "executing",
        "comparing",
        "publishing",
        "stopping",
    }
)
_PREPUBLICATION_PHASES = frozenset(
    {"accepted", "planning", "preflight", "executing", "comparing"}
)
_PUBLICATION_STAGES = frozenset(
    {"not_ready", "ready", "publishing", "result_committed", "complete"}
)


_DDL = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('log', 'entry')),
    target_entry TEXT,
    include_all INTEGER NOT NULL CHECK(include_all IN (0, 1)),
    jobs INTEGER NOT NULL CHECK(jobs >= 1),
    execution_timeout_seconds INTEGER NOT NULL CHECK(execution_timeout_seconds >= 1),
    accepted_at TEXT NOT NULL,
    run_path TEXT NOT NULL,
    workspace_path TEXT NOT NULL,
    diagnostics_path TEXT NOT NULL,
    CHECK((target_kind = 'log' AND target_entry IS NULL) OR
          (target_kind = 'entry' AND target_entry IS NOT NULL))
);
CREATE TABLE run_state (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE RESTRICT,
    status TEXT CHECK(status IN ('complete', 'stopped', 'failed')),
    phase TEXT CHECK(phase IN ('accepted', 'planning', 'preflight', 'executing', 'comparing', 'publishing', 'stopping')),
    stop_requested_at TEXT,
    started_at TEXT,
    resumed_at TEXT,
    stopped_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    completed_executions INTEGER NOT NULL CHECK(completed_executions >= 0),
    matched INTEGER NOT NULL CHECK(matched >= 0),
    changed INTEGER NOT NULL CHECK(changed >= 0),
    failed INTEGER NOT NULL CHECK(failed >= 0),
    comparison_failed INTEGER NOT NULL CHECK(comparison_failed >= 0),
    skipped INTEGER NOT NULL CHECK(skipped >= 0),
    latest_execution_entry TEXT,
    latest_execution_cid TEXT,
    latest_execution_id TEXT,
    latest_execution_code TEXT,
    latest_execution_message TEXT,
    latest_execution_recorded_at TEXT,
    operational_code TEXT,
    operational_message TEXT,
    operational_recorded_at TEXT,
    CHECK((status IS NULL AND phase IS NOT NULL) OR
          (status IS NOT NULL AND phase IS NULL)),
    CHECK((latest_execution_entry IS NULL AND latest_execution_cid IS NULL AND latest_execution_id IS NULL AND
           latest_execution_code IS NULL AND latest_execution_message IS NULL AND
           latest_execution_recorded_at IS NULL) OR
          (latest_execution_entry IS NOT NULL AND latest_execution_cid IS NOT NULL AND latest_execution_id IS NOT NULL AND
           latest_execution_code IS NOT NULL AND latest_execution_message IS NOT NULL AND
           latest_execution_recorded_at IS NOT NULL)),
    CHECK((operational_code IS NULL AND operational_message IS NULL AND
           operational_recorded_at IS NULL) OR
          (operational_code IS NOT NULL AND operational_message IS NOT NULL AND
           operational_recorded_at IS NOT NULL))
);
CREATE TABLE accepted_admission (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE RESTRICT,
    validation_id TEXT NOT NULL,
    validation_result_id TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    evaluated_at TEXT NOT NULL
);
CREATE TABLE accepted_admission_groups (
    run_id TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK(disposition IN ('admitted', 'excluded')),
    position INTEGER NOT NULL CHECK(position >= 0),
    entry TEXT NOT NULL,
    group_id TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    PRIMARY KEY(run_id, disposition, position),
    FOREIGN KEY(run_id) REFERENCES accepted_admission(run_id) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_commands (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL CHECK(command_pk >= 1),
    entry TEXT NOT NULL,
    cid TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    selection TEXT NOT NULL CHECK(selection IN ('run', 'not_needed', 'unchanged', 'blocked', 'policy')),
    auto_reproduce INTEGER NOT NULL CHECK(auto_reproduce IN (0, 1)),
    exclusive INTEGER NOT NULL CHECK(exclusive IN (0, 1)),
    queued INTEGER NOT NULL CHECK(queued IN (0, 1)),
    accepted_requires_reproduction INTEGER NOT NULL CHECK(accepted_requires_reproduction IN (0, 1)),
    prior_disposition TEXT CHECK(prior_disposition IN ('failed', 'blocked')),
    source_digest TEXT,
    entry_root TEXT NOT NULL,
    project_root TEXT NOT NULL,
    cwd TEXT NOT NULL,
    script TEXT NOT NULL,
    last_run_at TEXT,
    runner TEXT NOT NULL,
    environment_profile TEXT NOT NULL,
    execution_contract TEXT NOT NULL,
    details_json TEXT NOT NULL,
    data_declaration_json TEXT,
    PRIMARY KEY(run_id, command_pk),
    UNIQUE(run_id, entry, cid, execution_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE RESTRICT,
    CHECK((selection = 'unchanged' AND prior_disposition IS NOT NULL) OR
          (selection <> 'unchanged' AND prior_disposition IS NULL)),
    CHECK(selection IN ('not_needed', 'policy') OR source_digest IS NOT NULL)
) WITHOUT ROWID;
CREATE TABLE accepted_recipe_parameters (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    value TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk, position),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_commands(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_parameter_roles (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    selector TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('input', 'output', 'ordinary')),
    PRIMARY KEY(run_id, command_pk, selector),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_commands(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_recipe_environment (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk, name),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_commands(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_materials (
    run_id TEXT NOT NULL,
    material_pk INTEGER NOT NULL CHECK(material_pk >= 1),
    role TEXT NOT NULL CHECK(role IN ('boundary', 'comparison_baseline', 'input', 'script', 'code')),
    identity TEXT NOT NULL,
    kind TEXT NOT NULL,
    selection_json TEXT,
    fingerprint_json TEXT NOT NULL,
    PRIMARY KEY(run_id, material_pk),
    UNIQUE(run_id, role, identity, fingerprint_json),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_command_inputs (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    name TEXT NOT NULL,
    material_pk INTEGER NOT NULL,
    PRIMARY KEY(run_id, command_pk, name),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_commands(run_id, command_pk) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, material_pk) REFERENCES accepted_materials(run_id, material_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_command_outputs (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    artifact TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('file', 'directory')),
    material_pk INTEGER NOT NULL,
    PRIMARY KEY(run_id, command_pk, artifact),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_commands(run_id, command_pk) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, material_pk) REFERENCES accepted_materials(run_id, material_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_command_code (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    path TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('script', 'code')),
    material_pk INTEGER NOT NULL,
    PRIMARY KEY(run_id, command_pk, path),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_commands(run_id, command_pk) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, material_pk) REFERENCES accepted_materials(run_id, material_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_comparison_materials (
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    material_pk INTEGER NOT NULL,
    PRIMARY KEY(run_id, position),
    FOREIGN KEY(run_id, material_pk) REFERENCES accepted_materials(run_id, material_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_executions (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    plan_order INTEGER NOT NULL CHECK(plan_order >= 1),
    run_path TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk),
    UNIQUE(run_id, plan_order),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_commands(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_execution_dependencies (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    dependency_command_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    PRIMARY KEY(run_id, command_pk, position),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_executions(run_id, command_pk) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, dependency_command_pk) REFERENCES accepted_executions(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_execution_outputs (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    output_command_position INTEGER NOT NULL CHECK(output_command_position >= 0),
    artifact TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk, output_command_position),
    UNIQUE(run_id, command_pk, artifact),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_executions(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_execution_claims (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    claim_kind TEXT NOT NULL CHECK(claim_kind IN ('read', 'write', 'writable')),
    position INTEGER NOT NULL CHECK(position >= 0),
    path TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk, claim_kind, position),
    UNIQUE(run_id, command_pk, claim_kind, path),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_executions(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_cases (
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    entry TEXT NOT NULL,
    artifact TEXT NOT NULL,
    command_pk INTEGER,
    disposition TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY(run_id, position),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_commands(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_boundaries (
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    kind TEXT NOT NULL CHECK(kind IN ('origin', 'cross_entry', 'non_automatic', 'outside_queue')),
    entry TEXT,
    name TEXT,
    artifact TEXT,
    selection_json TEXT,
    fingerprint_json TEXT NOT NULL,
    PRIMARY KEY(run_id, position),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_failures (
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    entry TEXT NOT NULL,
    artifact TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome = 'failed'),
    reason TEXT NOT NULL,
    dependencies_json TEXT NOT NULL,
    PRIMARY KEY(run_id, position),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_comparisons (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    artifact TEXT NOT NULL,
    definition_identity TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk, artifact),
    UNIQUE(run_id, definition_identity),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_commands(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_comparison_records (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    artifact TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    record_json TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk, artifact, position),
    FOREIGN KEY(run_id, command_pk, artifact) REFERENCES accepted_comparisons(run_id, command_pk, artifact) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_evidence_observations (
    run_id TEXT NOT NULL,
    observation_pk INTEGER NOT NULL CHECK(observation_pk >= 1),
    entry TEXT NOT NULL,
    record_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    kind TEXT NOT NULL,
    selection_json TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL,
    PRIMARY KEY(run_id, observation_pk),
    UNIQUE(run_id, entry, record_id, resource),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE accepted_evidence_comparison_links (
    run_id TEXT NOT NULL,
    observation_pk INTEGER NOT NULL,
    definition_identity TEXT NOT NULL,
    PRIMARY KEY(run_id, observation_pk, definition_identity),
    FOREIGN KEY(run_id, observation_pk) REFERENCES accepted_evidence_observations(run_id, observation_pk) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, definition_identity) REFERENCES accepted_comparisons(run_id, definition_identity) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE run_owner (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE RESTRICT,
    supervisor_pid INTEGER NOT NULL CHECK(supervisor_pid >= 1),
    state TEXT NOT NULL CHECK(state IN ('running', 'stopped', 'exited')),
    registered_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL
);
CREATE TABLE execution_checkpoints (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active', 'succeeded', 'failed', 'stopped')),
    permit_id TEXT,
    released_permit_id TEXT,
    checkpointed_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    elapsed_seconds REAL NOT NULL CHECK(elapsed_seconds >= 0),
    failure_code TEXT,
    failure_message TEXT,
    failure_recorded_at TEXT,
    stdout_path TEXT,
    stderr_path TEXT,
    scratch_path TEXT,
    PRIMARY KEY(run_id, command_pk),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_executions(run_id, command_pk) ON DELETE RESTRICT,
    CHECK((state = 'active' AND finished_at IS NULL AND failure_code IS NULL AND failure_message IS NULL AND failure_recorded_at IS NULL) OR
          (state = 'succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND failure_code IS NULL AND failure_message IS NULL AND failure_recorded_at IS NULL) OR
          (state = 'failed' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND failure_code IS NOT NULL AND failure_message IS NOT NULL AND failure_recorded_at IS NOT NULL) OR
          (state = 'stopped' AND finished_at IS NULL AND failure_code IS NOT NULL AND failure_message IS NOT NULL AND failure_recorded_at IS NOT NULL))
);
CREATE TABLE checkpoint_outputs (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    artifact TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk, artifact),
    FOREIGN KEY(run_id, command_pk) REFERENCES execution_checkpoints(run_id, command_pk) ON DELETE CASCADE,
    FOREIGN KEY(run_id, command_pk, artifact) REFERENCES accepted_command_outputs(run_id, command_pk, artifact) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE workers (
    run_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    parent_worker_id TEXT,
    command_pk INTEGER,
    pid INTEGER NOT NULL CHECK(pid >= 1),
    state TEXT NOT NULL CHECK(state IN ('running', 'exited')),
    registered_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    PRIMARY KEY(run_id, worker_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_executions(run_id, command_pk) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, parent_worker_id) REFERENCES workers(run_id, worker_id) DEFERRABLE INITIALLY DEFERRED
) WITHOUT ROWID;
CREATE TABLE execution_effects (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    comparison_recorded_at TEXT,
    requirement_cleared_at TEXT,
    PRIMARY KEY(run_id, command_pk),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_executions(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE staged_executions (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    complete INTEGER NOT NULL CHECK(complete IN (0, 1)),
    retained_bytes INTEGER NOT NULL CHECK(retained_bytes >= 0),
    workspace_path TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk),
    FOREIGN KEY(run_id, command_pk) REFERENCES accepted_executions(run_id, command_pk) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE staged_diagnostics (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    path TEXT NOT NULL,
    PRIMARY KEY(run_id, command_pk, position),
    FOREIGN KEY(run_id, command_pk) REFERENCES staged_executions(run_id, command_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE artifact_comparisons (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    artifact TEXT NOT NULL,
    kind TEXT NOT NULL,
    available INTEGER NOT NULL CHECK(available IN (0, 1)),
    staged_path TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('matched', 'changed', 'failed', 'comparison_failed', 'skipped')),
    reason TEXT,
    profile TEXT,
    expected_json TEXT,
    regenerated_json TEXT,
    evidence_definition TEXT,
    PRIMARY KEY(run_id, command_pk, artifact),
    FOREIGN KEY(run_id, command_pk) REFERENCES staged_executions(run_id, command_pk) ON DELETE CASCADE,
    FOREIGN KEY(run_id, command_pk, artifact) REFERENCES accepted_command_outputs(run_id, command_pk, artifact) ON DELETE RESTRICT
) WITHOUT ROWID;
CREATE TABLE artifact_comparison_evidence (
    run_id TEXT NOT NULL,
    command_pk INTEGER NOT NULL,
    artifact TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    record_id TEXT NOT NULL,
    retained_json TEXT NOT NULL,
    regenerated_json TEXT NOT NULL,
    tolerance_json TEXT NOT NULL,
    matched INTEGER NOT NULL CHECK(matched IN (0, 1)),
    PRIMARY KEY(run_id, command_pk, artifact, position),
    FOREIGN KEY(run_id, command_pk, artifact) REFERENCES artifact_comparisons(run_id, command_pk, artifact) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE publication_state (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE RESTRICT,
    stage TEXT NOT NULL CHECK(stage IN ('not_ready', 'ready', 'publishing', 'result_committed', 'complete')),
    publication_identity TEXT,
    result_generation INTEGER CHECK(result_generation >= 1),
    report_generation INTEGER CHECK(report_generation >= 1),
    failure_code TEXT,
    failure_message TEXT,
    failure_recorded_at TEXT,
    updated_at TEXT NOT NULL,
    CHECK((failure_code IS NULL AND failure_message IS NULL AND failure_recorded_at IS NULL) OR
          (failure_code IS NOT NULL AND failure_message IS NOT NULL AND failure_recorded_at IS NOT NULL)),
    CHECK((stage = 'not_ready' AND publication_identity IS NULL AND result_generation IS NULL AND report_generation IS NULL) OR
          (stage IN ('ready', 'publishing') AND publication_identity IS NOT NULL AND result_generation IS NULL AND report_generation IS NULL) OR
          (stage = 'result_committed' AND publication_identity IS NOT NULL AND result_generation IS NOT NULL AND report_generation IS NULL) OR
          (stage = 'complete' AND publication_identity IS NOT NULL AND result_generation IS NOT NULL AND report_generation IS NOT NULL))
);
CREATE INDEX accepted_commands_identity ON accepted_commands(run_id, entry, cid, execution_id);
CREATE INDEX accepted_executions_order ON accepted_executions(run_id, plan_order);
CREATE INDEX execution_checkpoints_state ON execution_checkpoints(run_id, state, command_pk);
CREATE INDEX workers_state ON workers(run_id, state, command_pk);
CREATE INDEX artifact_comparisons_outcome ON artifact_comparisons(run_id, outcome, command_pk, artifact);
"""


class JobStoreError(RuntimeError):
    """Base class for a bounded durable job-storage failure."""

    code = "reproduction.run.invalid"

    def __init__(self, message: str, *, code: str | None = None):
        self.code = code or type(self).code
        super().__init__(message[:1024])


class JobStoreMissingError(JobStoreError):
    """The requested current-format job store is absent."""

    code = "reproduction.run.missing"


class JobStoreExistsError(JobStoreError):
    """A run has already accepted its single immutable plan."""

    code = "reproduction.run.exists"


class JobStoreBusyError(JobStoreError):
    """The run-state mutex or SQLite writer is already held."""

    code = "reproduction.run.busy"


class JobStoreSymlinkError(JobStoreError):
    """A run-state path or SQLite companion is a symlink."""

    code = "reproduction.run.path_unsafe"


class JobStoreUnsupportedError(JobStoreError):
    """The durable database uses an unsupported schema version."""

    code = "reproduction.run.unsupported"


class JobStoreMalformedError(JobStoreError):
    """The durable database or a selected typed projection is malformed."""

    code = "reproduction.run.invalid"


class JobStoreInvariantError(JobStoreError):
    """Stored rows violate a durable cross-row invariant."""

    code = "reproduction.run.invariant"


class JobStoreTransitionError(JobStoreError):
    """A compare-and-set lifecycle transition did not match prior state."""

    code = "reproduction.run.transition"


@dataclass(frozen=True)
class AcceptedJob:
    """Immutable acceptance inputs for one new fixed-plan run."""

    run_id: str
    plan: ReproductionPlan
    accepted_at: str
    run_path: str
    workspace_path: str = "workspace"
    diagnostics_path: str = "diagnostics"


@dataclass(frozen=True)
class RunIdentity:
    """The immutable identity and canonical location of one accepted run."""

    run_id: str
    run_path: str


@dataclass(frozen=True)
class CheckpointOutput:
    """One observed output owned by a terminal execution checkpoint."""

    artifact: str
    fingerprint: Mapping[str, object]


@dataclass(frozen=True)
class ExecutionPermitAttachment:
    """Compare-and-set request that attaches one scheduler permit."""

    entry: str
    cid: str
    execution_id: str
    permit_id: str
    checkpointed_at: str
    expected_state: Literal["absent", "stopped"] = "absent"


@dataclass(frozen=True)
class ExecutionStart:
    """Compare-and-set request that records the immediately pending launch."""

    entry: str
    cid: str
    execution_id: str
    permit_id: str
    checkpointed_at: str
    started_at: str
    scratch_path: str
    elapsed_seconds: float = 0.0
    stdout_path: str | None = None
    stderr_path: str | None = None


@dataclass(frozen=True)
class ExecutionTerminal:
    """Atomic terminal state, timing, diagnostic, and output update."""

    entry: str
    cid: str
    execution_id: str
    permit_id: str
    state: Literal["succeeded", "failed", "stopped"]
    checkpointed_at: str
    finished_at: str | None
    elapsed_seconds: float
    outputs: tuple[CheckpointOutput, ...] = ()
    failure_code: str | None = None
    failure_message: str | None = None
    failure_recorded_at: str | None = None
    expected_state: Literal["active"] = "active"
    workers: tuple[WorkerRecord, ...] = ()


@dataclass(frozen=True)
class CheckpointProjection:
    """Bounded durable checkpoint projection for one accepted execution."""

    entry: str
    cid: str
    execution_id: str
    state: str
    permit_id: str | None
    released_permit_id: str | None
    checkpointed_at: str
    started_at: str | None
    finished_at: str | None
    elapsed_seconds: float
    failure_code: str | None
    failure_message: str | None
    failure_recorded_at: str | None
    stdout_path: str | None
    stderr_path: str | None
    scratch_path: str | None
    outputs: tuple[CheckpointOutput, ...]


@dataclass(frozen=True)
class DiagnosticProjection:
    """One bounded current execution or run-level operational diagnostic."""

    code: str
    message: str
    recorded_at: str
    entry: str | None = None
    cid: str | None = None
    execution_id: str | None = None


@dataclass(frozen=True)
class RunStatus:
    """Bounded lifecycle, ownership, and checkpoint projection."""

    run_id: str
    summary: str
    target: Mapping[str, object]
    include_all: bool
    jobs: int
    execution_timeout_seconds: int
    accepted_at: str
    run_path: str
    status: str | None
    phase: str | None
    stop_requested_at: str | None
    started_at: str | None
    resumed_at: str | None
    stopped_at: str | None
    finished_at: str | None
    updated_at: str
    completed_executions: int
    artifact_outcomes: Mapping[str, int]
    latest_execution_diagnostic: DiagnosticProjection | None
    operational_failure: DiagnosticProjection | None
    checkpoints: tuple[CheckpointProjection, ...]
    workers: tuple[tuple[ExecutionIdentity | None, WorkerRecord], ...]
    total_executions: int


@dataclass(frozen=True)
class ExecutionIdentity:
    """One accepted execution identity within the run selected by its path."""

    entry: str
    cid: str
    execution_id: str


@dataclass(frozen=True)
class ExecutionReadiness:
    """One execution's bounded direct-dependency scheduling projection."""

    identity: ExecutionIdentity
    disposition: Literal["waiting", "ready", "dependency_failed"]
    pending_dependencies: tuple[ExecutionIdentity, ...]
    failed_dependencies: tuple[ExecutionIdentity, ...]


@dataclass(frozen=True)
class RunControl:
    """Small lifecycle projection polled by execution control paths."""

    status: str | None
    phase: str | None
    stop_requested_at: str | None
    operational_code: str | None


@dataclass(frozen=True)
class RequirementEffectProjection:
    """Identity-local comparison and external-effect checkpoint state."""

    accepted_requires_reproduction: bool
    comparison_recorded_at: str | None
    requirement_cleared_at: str | None


@dataclass(frozen=True)
class AcceptedSchedulingProjection:
    """Bounded immutable scheduling facts for one accepted execution."""

    run_id: str
    identity: ExecutionIdentity
    plan_order: int
    kind: Literal["ordinary", "exclusive"]
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    run_path: str
    writable_paths: tuple[str, ...]


@dataclass(frozen=True)
class WorkerRecord:
    """One supervised worker observation retained for a run."""

    worker_id: str
    parent_worker_id: str | None
    pid: int
    state: Literal["running", "exited"]
    registered_at: str
    last_observed_at: str


@dataclass(frozen=True)
class RecoveryWorkerObservation:
    """One process still carrying this run's durable worker marker."""

    identity: ExecutionIdentity | None
    worker: WorkerRecord


@dataclass(frozen=True)
class RunOwner:
    """The single current supervisor identity for a run."""

    supervisor_pid: int
    state: Literal["running", "stopped", "exited"]
    registered_at: str
    last_observed_at: str


@dataclass(frozen=True)
class SchedulerOwnerProjection:
    """Bounded lifecycle, permit, owner, and live-worker scheduler projection."""

    run_id: str
    status: str | None
    phase: str | None
    stop_requested_at: str | None
    owner: RunOwner | None
    checkpoints: tuple[CheckpointProjection, ...]
    running_workers: tuple[tuple[ExecutionIdentity | None, WorkerRecord], ...]


@dataclass(frozen=True)
class ComparisonEvidence:
    """One retained-versus-regenerated evidence comparison detail."""

    record_id: str
    retained: Mapping[str, object]
    regenerated: Mapping[str, object]
    tolerance: Mapping[str, object]
    matched: bool


@dataclass(frozen=True)
class ArtifactComparisonWrite:
    """One artifact comparison plus the accepted identities it consumed."""

    artifact: str
    kind: str
    available: bool
    staged_path: str | None
    outcome: Literal["matched", "changed", "failed", "comparison_failed", "skipped"]
    reason: str | None
    profile: str | None
    expected: Mapping[str, object] | None
    regenerated: Mapping[str, object] | None
    evidence_definition: str | None
    accepted_baseline: Mapping[str, object]
    accepted_definition: str | None = None
    evidence: tuple[ComparisonEvidence, ...] = ()


@dataclass(frozen=True)
class ExecutionComparisonWrite:
    """Complete replacement for one execution's staged comparison children."""

    entry: str
    cid: str
    execution_id: str
    complete: bool
    retained_bytes: int
    workspace_path: str
    diagnostics: tuple[str, ...]
    artifacts: tuple[ArtifactComparisonWrite, ...]
    recorded_at: str


@dataclass(frozen=True)
class RequirementEffect:
    """Checkpoint that the accepted execution's external requirement is clear."""

    entry: str
    cid: str
    execution_id: str
    recorded_at: str


@dataclass(frozen=True)
class PublicationFailure:
    """One bounded publication failure retained for retry diagnosis."""

    code: str
    message: str
    recorded_at: str


@dataclass(frozen=True)
class RunStopRequest:
    """First durable user stop request for one active run."""

    requested_at: str


@dataclass(frozen=True)
class RunFailure:
    """Exact operational failure intent that requires quiescent cleanup."""

    code: str
    message: str
    recorded_at: str


@dataclass(frozen=True)
class RunStopCompletion:
    """Time at which quiescent stop cleanup reached its terminal state."""

    terminal_at: str


@dataclass(frozen=True)
class RunResumeRequest:
    """Activation request for one quiescent user-stopped run."""

    resumed_at: str


@dataclass(frozen=True)
class PublicationResumeRequest:
    """Exact publication-only retry identity and stage."""

    publication_identity: str
    expected_stage: Literal["ready", "result_committed"]
    resumed_at: str


@dataclass(frozen=True)
class PublicationStateProjection:
    """Bounded compare-and-set state for result and report publication."""

    stage: str
    publication_identity: str | None
    result_generation: int | None
    report_generation: int | None
    failure_code: str | None
    failure_message: str | None
    failure_recorded_at: str | None
    updated_at: str


@dataclass(frozen=True)
class PublicationProjection:
    """Immutable plan and terminal rows required to construct publication."""

    identity: RunIdentity
    plan: ReproductionPlan
    accepted_at: str
    finished_at: str | None
    status: str | None
    checkpoints: tuple[CheckpointProjection, ...]
    comparisons: tuple[ExecutionComparisonWrite, ...]
    publication: PublicationStateProjection


@dataclass(frozen=True)
class JobAudit:
    """Whole-store integrity result returned only by explicit audit."""

    run_id: str
    table_rows: Mapping[str, int]
    integrity_check: str
    foreign_key_violations: int
    data_bytes: int


def create_job(run_root: Path, accepted_job: AcceptedJob) -> RunIdentity:
    """Create one durable job and atomically accept its only plan."""

    run_root = _regular_run_root(run_root)
    _validate_accepted_job(run_root, accepted_job)
    with _job_mutex(run_root):
        path = _checked_state_path(run_root, writable=True)
        if path.exists():
            raise JobStoreExistsError(f"job state already exists: {path}")
        try:
            db = _open_database(path, mode="rwc", allow_uninitialized=True)
            try:
                db.execute("BEGIN IMMEDIATE")
                _initialize_schema(db)
                _insert_accepted_job(db, accepted_job)
                _check_database_size(db)
                _before_commit("acceptance", db)
                db.commit()
                _check_store_size(path)
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()
        except BaseException as error:
            _remove_failed_creation(path)
            _raise_storage_error(error)
    return RunIdentity(accepted_job.run_id, accepted_job.run_path)


def load_accepted_plan(run_root: Path) -> ReproductionPlan:
    """Reconstruct the immutable accepted plan from normalized rows."""

    with open_locked_job(run_root) as store:
        return store.load_accepted_plan()


def load_run_status(run_root: Path) -> RunStatus:
    """Load one bounded lifecycle projection from one SQLite snapshot."""

    with open_locked_job(run_root) as store:
        return store.load_run_status()


def load_execution_readiness(
    run_root: Path, identity: ExecutionIdentity
) -> ExecutionReadiness:
    """Read only one execution's ordered durable dependency disposition."""

    with open_locked_job(run_root) as store:
        return store.load_execution_readiness(identity)


def load_execution_checkpoint(
    run_root: Path, identity: ExecutionIdentity
) -> CheckpointProjection | None:
    """Read one accepted execution checkpoint and its output children."""

    with open_locked_job(run_root) as store:
        return store.load_execution_checkpoint(identity)


def load_run_control(run_root: Path) -> RunControl:
    """Read only the lifecycle fields required by an execution poll."""

    with open_locked_job(run_root) as store:
        return store.load_run_control()


def load_requirement_effect(
    run_root: Path, identity: ExecutionIdentity
) -> RequirementEffectProjection:
    """Read one execution's comparison and external-effect checkpoint."""

    with open_locked_job(run_root) as store:
        return store.load_requirement_effect(identity)


def load_accepted_scheduling(
    run_root: Path, identity: ExecutionIdentity
) -> AcceptedSchedulingProjection:
    """Read one execution's immutable logical scheduler inputs."""

    with open_locked_job(run_root) as store:
        return store.load_accepted_scheduling(identity)


def recognize_run_directory(
    run_root: Path,
) -> Literal["current", "historical_unsupported", "absent"]:
    """Classify a canonical run directory without opening historical state."""

    if not run_root.exists() or run_root.is_symlink() or not run_root.is_dir():
        return "absent"
    parts = run_root.parts
    if len(parts) < 4 or not is_canonical_run_path(
        PurePosixPath(*parts[-4:]).as_posix()
    ):
        return "absent"
    return (
        "current" if (run_root / "state.sqlite").exists() else "historical_unsupported"
    )


def record_execution_start(run_root: Path, start: ExecutionStart) -> None:
    """Record the immediately pending launch for one attached permit."""

    with open_locked_job(run_root) as store:
        store.record_execution_start(start)


def attach_execution_permit(
    run_root: Path, attachment: ExecutionPermitAttachment
) -> None:
    """Attach one committed scheduler permit to its accepted execution."""

    with open_locked_job(run_root) as store:
        store.attach_execution_permit(attachment)


def clear_execution_permit(
    run_root: Path,
    identity: ExecutionIdentity,
    permit_id: str,
    expected_state: Literal["succeeded", "failed", "stopped"],
    updated_at: str,
) -> None:
    """Clear one released scheduler permit from its terminal checkpoint."""

    with open_locked_job(run_root) as store:
        store.clear_execution_permit(identity, permit_id, expected_state, updated_at)


def clear_execution_scratch(
    run_root: Path, identity: ExecutionIdentity, scratch_path: str
) -> None:
    """Clear one identity's exact scratch ownership after successful cleanup."""

    with open_locked_job(run_root) as store:
        store.clear_execution_scratch(identity, scratch_path)


def request_run_stop(run_root: Path, request: RunStopRequest) -> None:
    """Record durable stop intent for one active run."""

    with open_locked_job(run_root) as store:
        store.request_run_stop(request)


def request_run_failure(run_root: Path, failure: RunFailure) -> None:
    """Record exact operational failure intent before cleanup begins."""

    with open_locked_job(run_root) as store:
        store.request_run_failure(failure)


def finish_run_stop(run_root: Path, completion: RunStopCompletion) -> None:
    """Commit stopped status after every worker and permit is gone."""

    with open_locked_job(run_root) as store:
        store.finish_run_stop(completion)


def begin_run_resume(run_root: Path, request: RunResumeRequest) -> None:
    """Compare-and-set one fully stopped run back to accepted phase."""

    with open_locked_job(run_root) as store:
        store.begin_run_resume(request)


def begin_publication_resume(run_root: Path, request: PublicationResumeRequest) -> None:
    """Reactivate only one exact publication-only retry state."""

    with open_locked_job(run_root) as store:
        store.begin_publication_resume(request)


def record_execution_terminal(run_root: Path, terminal: ExecutionTerminal) -> None:
    """Commit terminal state and outputs for only one execution identity."""

    with open_locked_job(run_root) as store:
        store.record_execution_terminal(terminal)


def replace_run_owner(run_root: Path, owner: RunOwner) -> None:
    """Replace the single current supervisor identity without retaining attempts."""

    with open_locked_job(run_root) as store:
        store.replace_run_owner(owner)


def replace_execution_workers(
    run_root: Path,
    identity: ExecutionIdentity,
    workers: Sequence[WorkerRecord],
) -> None:
    """Replace worker observations for only the named execution identity."""

    with open_locked_job(run_root) as store:
        store.replace_execution_workers(identity, workers)


def replace_recovery_workers(
    run_root: Path,
    observations: Sequence[RecoveryWorkerObservation],
    *,
    observed_at: str,
) -> None:
    """Replace live recovery observations inside the accepted SQLite authority."""

    with open_locked_job(run_root) as store:
        store.replace_recovery_workers(observations, observed_at=observed_at)


def load_scheduler_owner(run_root: Path) -> SchedulerOwnerProjection:
    """Load the bounded run ownership projection used by scheduler recovery."""

    with open_locked_job(run_root) as store:
        return store.load_scheduler_owner()


def load_run_owner(run_root: Path) -> RunOwner | None:
    """Load only the durable supervisor row for recovery diagnosis."""

    with open_locked_job(run_root) as store:
        return store.load_run_owner()


def record_execution_comparison(
    run_root: Path, comparison: ExecutionComparisonWrite
) -> None:
    """Replace only one execution's staged comparison and evidence children."""

    with open_locked_job(run_root) as store:
        store.record_execution_comparison(comparison)


def record_requirement_effect(run_root: Path, effect: RequirementEffect) -> None:
    """Record the completed external requirement mutation for one execution."""

    with open_locked_job(run_root) as store:
        store.record_requirement_effect(effect)


def load_publication_projection(run_root: Path) -> PublicationProjection:
    """Load the bounded immutable and terminal state required for publication."""

    with open_locked_job(run_root) as store:
        return store.load_publication_projection()


def prepare_publication(
    run_root: Path, publication_identity: str, *, updated_at: str
) -> None:
    """Compare-and-set an unprepared terminal job to publication-ready."""

    with open_locked_job(run_root) as store:
        store.prepare_publication(publication_identity, updated_at=updated_at)


def begin_publication(
    run_root: Path, publication_identity: str, *, updated_at: str
) -> None:
    """Compare-and-set a ready job immediately before its result transaction."""

    with open_locked_job(run_root) as store:
        store.begin_publication(publication_identity, updated_at=updated_at)


def record_result_commit(
    run_root: Path,
    publication_identity: str,
    generation: int,
    *,
    updated_at: str,
) -> None:
    """Record the result generation committed for one publishing identity."""

    with open_locked_job(run_root) as store:
        store.record_result_commit(
            publication_identity, generation, updated_at=updated_at
        )


def record_report_commit(
    run_root: Path,
    publication_identity: str,
    generation: int,
    *,
    updated_at: str,
) -> None:
    """Record report materialization and complete the durable job."""

    with open_locked_job(run_root) as store:
        store.record_report_commit(
            publication_identity, generation, updated_at=updated_at
        )


def record_publication_failure(
    run_root: Path,
    expected_stage: str,
    failure: PublicationFailure,
) -> None:
    """Record a retryable failure without creating publication lineage."""

    with open_locked_job(run_root) as store:
        store.record_publication_failure(expected_stage, failure)


def audit_job_state(run_root: Path) -> JobAudit:
    """Validate the complete durable store and every cross-row invariant."""

    with open_locked_job(run_root) as store:
        return store.audit_job_state()


@contextmanager
def open_locked_job(
    run_root: Path,
    *,
    trace: Callable[[str], None] | None = None,
    update_hook: Callable[[str, str], None] | None = None,
) -> Iterator["LockedJobStore"]:
    """Hold the run-state mutex and yield typed operations, never a connection."""

    run_root = _regular_run_root(run_root)
    with _job_mutex(run_root):
        path = _checked_state_path(run_root, writable=False)
        db = _open_database(path, mode="rw")
        try:
            _validate_opened_run_root(db, run_root)
            if trace is not None:
                db.set_trace_callback(trace)
            if update_hook is not None:
                db.set_authorizer(_authorizer(update_hook))
            yield LockedJobStore(run_root, path, db)
        finally:
            db.set_trace_callback(None)
            db.set_authorizer(None)
            db.close()


class LockedJobStore:
    """Typed read and mutation surface held under one run-state mutex."""

    def __init__(self, run_root: Path, path: Path, db: sqlite3.Connection):
        self.run_root = run_root
        self.path = path
        self._db = db

    def load_accepted_plan(self) -> ReproductionPlan:
        """Reconstruct and validate the complete immutable plan in one snapshot."""

        with self._snapshot():
            plan = _load_accepted_plan(self._db)
        return plan

    def load_run_status(self) -> RunStatus:
        """Read only bounded status, checkpoint, and referenced plan rows."""

        with self._snapshot():
            status = _load_run_status(self._db)
        _require_bounded_status(status)
        return status

    def load_execution_readiness(
        self, identity: ExecutionIdentity
    ) -> ExecutionReadiness:
        """Read one accepted execution's direct durable dependencies only."""

        _require_execution_identity(identity.entry, identity.cid, identity.execution_id)
        with self._snapshot():
            run_id, command_pk = _command_identity(
                self._db,
                identity.entry,
                identity.cid,
                identity.execution_id,
                runnable=True,
            )
            rows = self._db.execute(
                "SELECT c.entry, c.cid, c.execution_id, p.state, d.dependency_command_pk "
                "FROM accepted_execution_dependencies d "
                "JOIN accepted_commands c ON c.run_id=d.run_id "
                "AND c.command_pk=d.dependency_command_pk "
                "LEFT JOIN execution_checkpoints p ON p.run_id=d.run_id "
                "AND p.command_pk=d.dependency_command_pk "
                "WHERE d.run_id=? AND d.command_pk=? ORDER BY d.position LIMIT ?",
                (run_id, command_pk, MAX_EXECUTION_DEPENDENCIES + 1),
            ).fetchall()
            if len(rows) > MAX_EXECUTION_DEPENDENCIES:
                raise JobStoreInvariantError(
                    "execution dependency count crossed its bound"
                )
            pending: list[ExecutionIdentity] = []
            failed: list[ExecutionIdentity] = []
            for row in rows:
                dependency = ExecutionIdentity(
                    cast(str, row["entry"]),
                    cast(str, row["cid"]),
                    cast(str, row["execution_id"]),
                )
                _require_execution_identity(
                    dependency.entry, dependency.cid, dependency.execution_id
                )
                if row["state"] == "failed" or _stored_dependency_skip(
                    self._db, run_id, int(row["dependency_command_pk"])
                ):
                    failed.append(dependency)
                elif row["state"] != "succeeded":
                    pending.append(dependency)
        if failed:
            return ExecutionReadiness(identity, "dependency_failed", (), tuple(failed))
        if pending:
            return ExecutionReadiness(identity, "waiting", tuple(pending), ())
        return ExecutionReadiness(identity, "ready", (), ())

    def load_execution_checkpoint(
        self, identity: ExecutionIdentity
    ) -> CheckpointProjection | None:
        """Read only one execution checkpoint and its bounded outputs."""

        _require_execution_identity(identity.entry, identity.cid, identity.execution_id)
        with self._snapshot():
            run_id, command_pk = _command_identity(
                self._db,
                identity.entry,
                identity.cid,
                identity.execution_id,
                runnable=True,
            )
            row = self._db.execute(
                "SELECT p.*, c.entry, c.cid, c.execution_id FROM execution_checkpoints p "
                "JOIN accepted_commands c USING(run_id, command_pk) "
                "WHERE p.run_id=? AND p.command_pk=?",
                (run_id, command_pk),
            ).fetchone()
            checkpoint = (
                None if row is None else _checkpoint_projection(self._db, run_id, row)
            )
        if checkpoint is not None:
            _validate_checkpoint_projection(checkpoint)
        return checkpoint

    def load_run_control(self) -> RunControl:
        """Read one row rather than reconstructing accumulated run history."""

        with self._snapshot():
            row = _sole_row(
                self._db,
                "SELECT status, phase, stop_requested_at, operational_code "
                "FROM run_state",
            )
        status = cast(str | None, row["status"])
        phase = cast(str | None, row["phase"])
        stop_requested_at = cast(str | None, row["stop_requested_at"])
        operational_code = cast(str | None, row["operational_code"])
        if status is not None and status not in _TERMINAL_RUN_STATUSES:
            raise JobStoreInvariantError("run control status is invalid")
        if phase is not None and phase not in _RUN_PHASES:
            raise JobStoreInvariantError("run control phase is invalid")
        if stop_requested_at is not None:
            _require_timestamp(stop_requested_at, "stop request time")
        if operational_code is not None and not _bounded_string(operational_code):
            raise JobStoreInvariantError("run control failure is invalid")
        return RunControl(status, phase, stop_requested_at, operational_code)

    def load_requirement_effect(
        self, identity: ExecutionIdentity
    ) -> RequirementEffectProjection:
        """Read one accepted execution's comparison/effect state only."""

        _require_execution_identity(identity.entry, identity.cid, identity.execution_id)
        with self._snapshot():
            run_id, command_pk = _command_identity(
                self._db,
                identity.entry,
                identity.cid,
                identity.execution_id,
                runnable=True,
            )
            row = self._db.execute(
                "SELECT c.accepted_requires_reproduction, "
                "e.comparison_recorded_at, e.requirement_cleared_at "
                "FROM accepted_commands c LEFT JOIN execution_effects e "
                "USING(run_id, command_pk) WHERE c.run_id=? AND c.command_pk=?",
                (run_id, command_pk),
            ).fetchone()
        if row is None:
            raise JobStoreInvariantError("accepted execution effect is missing")
        comparison_at = cast(str | None, row["comparison_recorded_at"])
        cleared_at = cast(str | None, row["requirement_cleared_at"])
        _require_optional_timestamp(comparison_at, "comparison recorded time")
        _require_optional_timestamp(cleared_at, "requirement cleared time")
        if cleared_at is not None and comparison_at is None:
            raise JobStoreInvariantError("requirement effect precedes comparison")
        return RequirementEffectProjection(
            bool(row["accepted_requires_reproduction"]), comparison_at, cleared_at
        )

    def load_accepted_scheduling(
        self, identity: ExecutionIdentity
    ) -> AcceptedSchedulingProjection:
        """Read one bounded immutable scheduling row without plan reconstruction."""

        _require_execution_identity(identity.entry, identity.cid, identity.execution_id)
        with self._snapshot():
            row = self._db.execute(
                "SELECT c.run_id, c.entry, c.cid, c.execution_id, c.exclusive, "
                "e.plan_order, e.run_path FROM accepted_commands c "
                "JOIN accepted_executions e USING(run_id, command_pk) "
                "WHERE c.entry=? AND c.cid=? AND c.execution_id=?",
                (identity.entry, identity.cid, identity.execution_id),
            ).fetchone()
            if row is None:
                raise JobStoreInvariantError(
                    "execution is not uniquely accepted and runnable"
                )
            claim_rows = self._db.execute(
                "SELECT claim_kind, position, path "
                "FROM accepted_execution_claims "
                "WHERE run_id=? AND command_pk=("
                "SELECT command_pk FROM accepted_commands "
                "WHERE run_id=? AND entry=? AND cid=? AND execution_id=?) "
                "ORDER BY claim_kind, position LIMIT ?",
                (
                    row["run_id"],
                    row["run_id"],
                    identity.entry,
                    identity.cid,
                    identity.execution_id,
                    3 * MAX_ACCEPTED_SCHEDULING_CLAIMS + 1,
                ),
            ).fetchall()
        if len(claim_rows) > 3 * MAX_ACCEPTED_SCHEDULING_CLAIMS:
            raise JobStoreInvariantError(
                "accepted scheduling claims crossed their bound"
            )
        claims: dict[str, list[str]] = {"read": [], "write": [], "writable": []}
        positions: dict[str, list[int]] = {"read": [], "write": [], "writable": []}
        for claim in claim_rows:
            kind = cast(str, claim["claim_kind"])
            path = cast(str, claim["path"])
            if (
                kind not in claims
                or not path
                or len(path.encode("utf-8")) > MAX_PATH_BYTES
            ):
                raise JobStoreInvariantError("accepted scheduling claim is invalid")
            claims[kind].append(path)
            positions[kind].append(int(claim["position"]))
        for kind in claims:
            if (
                positions[kind] != list(range(len(positions[kind])))
                or len(claims[kind]) > MAX_ACCEPTED_SCHEDULING_CLAIMS
            ):
                raise JobStoreInvariantError(
                    "accepted scheduling claim order is invalid"
                )
        run_id = cast(str, row["run_id"])
        if _RUN_ID_RE.fullmatch(run_id) is None:
            raise JobStoreInvariantError("accepted scheduling run identity is invalid")
        plan_order = int(row["plan_order"])
        if plan_order < 1:
            raise JobStoreInvariantError("accepted scheduling order is invalid")
        run_path = cast(str, row["run_path"])
        if not run_path or len(run_path.encode("utf-8")) > MAX_PATH_BYTES:
            raise JobStoreInvariantError("accepted scheduling run path is invalid")
        return AcceptedSchedulingProjection(
            run_id,
            identity,
            plan_order,
            "exclusive" if row["exclusive"] == 1 else "ordinary",
            tuple(claims["read"]),
            tuple(claims["write"]),
            run_path,
            tuple(claims["writable"]),
        )

    def attach_execution_permit(self, attachment: ExecutionPermitAttachment) -> None:
        """Attach one scheduler grant before any child may launch."""

        _validate_permit_attachment(attachment)
        with self._transaction("permit_attachment"):
            run_id, command_pk = _command_identity(
                self._db,
                attachment.entry,
                attachment.cid,
                attachment.execution_id,
                runnable=True,
            )
            existing = self._db.execute(
                "SELECT state, permit_id, scratch_path FROM execution_checkpoints "
                "WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            ).fetchone()
            lifecycle = _sole_row(
                self._db,
                "SELECT status, phase, stop_requested_at, operational_code "
                "FROM run_state WHERE run_id=?",
                (run_id,),
            )
            if (
                lifecycle["status"] is not None
                or lifecycle["phase"] not in _PREPUBLICATION_PHASES
                or lifecycle["stop_requested_at"] is not None
                or lifecycle["operational_code"] is not None
            ):
                raise JobStoreTransitionError("run cannot attach a permit")
            if existing is not None and existing["state"] == "active":
                if existing["permit_id"] == attachment.permit_id:
                    return
                raise JobStoreTransitionError("execution has a different permit")
            if attachment.expected_state == "absent":
                if existing is not None:
                    raise JobStoreTransitionError("permit expected no prior checkpoint")
                self._db.execute(
                    "INSERT INTO execution_checkpoints VALUES "
                    "(?, ?, 'active', ?, NULL, ?, NULL, NULL, 0, NULL, NULL, NULL, "
                    "NULL, NULL, NULL)",
                    (
                        run_id,
                        command_pk,
                        attachment.permit_id,
                        attachment.checkpointed_at,
                    ),
                )
            else:
                _require_resumable_checkpoint(existing)
                _require_no_running_worker(self._db, run_id, command_pk)
                self._db.execute(
                    "DELETE FROM checkpoint_outputs WHERE run_id=? AND command_pk=?",
                    (run_id, command_pk),
                )
                changed = self._db.execute(
                    "UPDATE execution_checkpoints SET state='active', permit_id=?, "
                    "checkpointed_at=?, failure_code=NULL, failure_message=NULL, "
                    "failure_recorded_at=NULL WHERE run_id=? AND command_pk=? "
                    "AND state='stopped' AND permit_id IS NULL AND scratch_path IS NULL",
                    (
                        attachment.permit_id,
                        attachment.checkpointed_at,
                        run_id,
                        command_pk,
                    ),
                ).rowcount
                if changed != 1:
                    raise JobStoreTransitionError(
                        "stopped permit attachment lost its state"
                    )
            self._db.execute(
                "UPDATE run_state SET updated_at=? WHERE run_id=?",
                (attachment.checkpointed_at, run_id),
            )

    def clear_execution_permit(
        self,
        identity: ExecutionIdentity,
        permit_id: str,
        expected_state: Literal["succeeded", "failed", "stopped"],
        updated_at: str,
    ) -> None:
        """Clear a released permit from only its terminal execution."""

        _validate_identity_value(identity, permit_id, "permit")
        if expected_state not in _TERMINAL_CHECKPOINT_STATES:
            raise JobStoreInvariantError("permit clear state is invalid")
        _require_timestamp(updated_at, "permit clear time")
        with self._transaction("permit_clear"):
            run_id, command_pk = _command_identity(
                self._db,
                identity.entry,
                identity.cid,
                identity.execution_id,
                runnable=True,
            )
            row = self._db.execute(
                "SELECT state, permit_id, released_permit_id "
                "FROM execution_checkpoints "
                "WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            ).fetchone()
            if row is None or row["state"] != expected_state:
                raise JobStoreTransitionError(
                    "permit clear requires a terminal checkpoint"
                )
            _require_no_running_worker(self._db, run_id, command_pk)
            if row["permit_id"] is None:
                if row["released_permit_id"] == permit_id:
                    return
                raise JobStoreTransitionError("execution released a different permit")
            if row["permit_id"] != permit_id:
                raise JobStoreTransitionError("execution has a different permit")
            changed = self._db.execute(
                "UPDATE execution_checkpoints SET permit_id=NULL, released_permit_id=? "
                "WHERE run_id=? AND command_pk=? AND permit_id=?",
                (permit_id, run_id, command_pk, permit_id),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("permit clear lost its state")
            self._db.execute(
                "UPDATE run_state SET updated_at=? WHERE run_id=?",
                (updated_at, run_id),
            )

    def clear_execution_scratch(
        self, identity: ExecutionIdentity, scratch_path: str
    ) -> None:
        """Clear exact scratch ownership after its directory is removed."""

        _require_execution_identity(identity.entry, identity.cid, identity.execution_id)
        _require_scratch_path(scratch_path, "scratch path")
        with self._transaction("scratch_clear"):
            run_id, command_pk = _command_identity(
                self._db,
                identity.entry,
                identity.cid,
                identity.execution_id,
                runnable=True,
            )
            row = self._db.execute(
                "SELECT state, scratch_path FROM execution_checkpoints "
                "WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            ).fetchone()
            if row is None or row["state"] not in _TERMINAL_CHECKPOINT_STATES:
                raise JobStoreTransitionError(
                    "scratch clear requires terminal checkpoint"
                )
            _require_no_running_worker(self._db, run_id, command_pk)
            if row["scratch_path"] is None:
                return
            if row["scratch_path"] != scratch_path:
                raise JobStoreTransitionError(
                    "execution has different scratch ownership"
                )
            self._db.execute(
                "UPDATE execution_checkpoints SET scratch_path=NULL "
                "WHERE run_id=? AND command_pk=? AND scratch_path=?",
                (run_id, command_pk, scratch_path),
            )

    def record_execution_start(self, start: ExecutionStart) -> None:
        """Record launch timing and scratch immediately before child launch."""

        _validate_execution_start(start)
        with self._transaction("execution_start"):
            run_id, command_pk = _command_identity(
                self._db, start.entry, start.cid, start.execution_id, runnable=True
            )
            row = self._db.execute(
                "SELECT state, permit_id, elapsed_seconds, scratch_path "
                "FROM execution_checkpoints WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            ).fetchone()
            if (
                row is None
                or row["state"] != "active"
                or row["permit_id"] != start.permit_id
                or float(row["elapsed_seconds"]) != start.elapsed_seconds
                or row["scratch_path"] is not None
            ):
                raise JobStoreTransitionError(
                    "execution start has no matching attachment"
                )
            changed = self._db.execute(
                "UPDATE execution_checkpoints SET checkpointed_at=?, "
                "started_at=COALESCE(started_at, ?), stdout_path=?, stderr_path=?, "
                "scratch_path=? WHERE run_id=? AND command_pk=? AND state='active' "
                "AND permit_id=? AND scratch_path IS NULL",
                (
                    start.checkpointed_at,
                    start.started_at,
                    start.stdout_path,
                    start.stderr_path,
                    start.scratch_path,
                    run_id,
                    command_pk,
                    start.permit_id,
                ),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("execution start lost its attachment")
            changed = self._db.execute(
                "UPDATE run_state SET phase='executing', "
                "started_at=COALESCE(started_at, ?), updated_at=? "
                "WHERE run_id=? AND status IS NULL AND phase IN "
                "('accepted','planning','preflight','executing','comparing') "
                "AND stop_requested_at IS NULL AND operational_code IS NULL",
                (start.started_at, start.checkpointed_at, run_id),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("terminal run cannot start execution")

    def request_run_stop(self, request: RunStopRequest) -> None:
        """Record stop intent without overwriting the first request time."""

        _require_timestamp(request.requested_at, "stop request time")
        with self._transaction("stop_request"):
            row = _sole_row(
                self._db,
                "SELECT run_id, status, phase, stop_requested_at, operational_code "
                "FROM run_state",
            )
            if row["status"] is not None:
                raise JobStoreTransitionError("terminal run cannot request stop")
            if row["phase"] == "stopping":
                return
            if row["stop_requested_at"] is not None:
                raise JobStoreInvariantError("run stop intent has an invalid phase")
            if row["operational_code"] is not None:
                raise JobStoreTransitionError(
                    "failed run intent cannot become user stop"
                )
            changed = self._db.execute(
                "UPDATE run_state SET phase='stopping', stop_requested_at=?, "
                "updated_at=? WHERE run_id=? AND status IS NULL "
                "AND phase<>'stopping' AND stop_requested_at IS NULL "
                "AND operational_code IS NULL",
                (
                    request.requested_at,
                    request.requested_at,
                    row["run_id"],
                ),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("stop request lost its state")

    def request_run_failure(self, failure: RunFailure) -> None:
        """Record one exact failed-terminal intent without losing user stop time."""

        _validate_run_failure(failure)
        with self._transaction("failure_request"):
            row = _sole_row(
                self._db,
                "SELECT run_id, status, phase, operational_code, operational_message, "
                "operational_recorded_at FROM run_state",
            )
            if row["status"] is not None:
                raise JobStoreTransitionError("terminal run cannot request failure")
            current = (
                row["operational_code"],
                row["operational_message"],
                row["operational_recorded_at"],
            )
            requested = (failure.code, failure.message, failure.recorded_at)
            if current == requested and row["phase"] == "stopping":
                return
            if current == requested:
                raise JobStoreInvariantError("run failure intent has an invalid phase")
            if any(value is not None for value in current):
                raise JobStoreTransitionError("run has different operational failure")
            changed = self._db.execute(
                "UPDATE run_state SET phase='stopping', operational_code=?, "
                "operational_message=?, operational_recorded_at=?, updated_at=? "
                "WHERE run_id=? AND status IS NULL AND operational_code IS NULL "
                "AND operational_message IS NULL AND operational_recorded_at IS NULL",
                (*requested, failure.recorded_at, row["run_id"]),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("failure request lost its state")

    def finish_run_stop(self, completion: RunStopCompletion) -> None:
        """Derive failed or stopped only after active ownership is reconciled."""

        _require_timestamp(completion.terminal_at, "stop completion time")
        with self._transaction("stop_finish"):
            row = _sole_row(
                self._db,
                "SELECT run_id, status, phase, operational_code, "
                "operational_message, operational_recorded_at FROM run_state",
            )
            if row["status"] is not None or row["phase"] != "stopping":
                raise JobStoreTransitionError("run is not stopping")
            run_id = cast(str, row["run_id"])
            _require_quiescent_run(self._db, run_id)
            operational = (
                row["operational_code"],
                row["operational_message"],
                row["operational_recorded_at"],
            )
            if any(value is None for value in operational) and any(
                value is not None for value in operational
            ):
                raise JobStoreInvariantError("run operational failure is incomplete")
            failed = all(value is not None for value in operational)
            status = "failed" if failed else "stopped"
            stopped_at = None if failed else completion.terminal_at
            finished_at = completion.terminal_at if failed else None
            changed = self._db.execute(
                "UPDATE run_state SET status=?, phase=NULL, stopped_at=?, "
                "finished_at=?, updated_at=? WHERE run_id=? AND status IS NULL "
                "AND phase='stopping'",
                (
                    status,
                    stopped_at,
                    finished_at,
                    completion.terminal_at,
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("stop completion lost its state")

    def begin_run_resume(self, request: RunResumeRequest) -> None:
        """Reactivate one fully stopped fixed-plan run without replanning."""

        _require_timestamp(request.resumed_at, "resume time")
        with self._transaction("run_resume"):
            row = _sole_row(
                self._db,
                "SELECT run_id, status, phase, finished_at, stopped_at, "
                "operational_code, operational_message, operational_recorded_at "
                "FROM run_state",
            )
            if (
                row["status"] != "stopped"
                or row["phase"] is not None
                or row["finished_at"] is not None
                or row["stopped_at"] is None
                or row["operational_code"] is not None
                or row["operational_message"] is not None
                or row["operational_recorded_at"] is not None
            ):
                raise JobStoreTransitionError("run is not stopped")
            run_id = cast(str, row["run_id"])
            _require_quiescent_run(self._db, run_id)
            changed = self._db.execute(
                "UPDATE run_state SET status=NULL, phase='accepted', "
                "stop_requested_at=NULL, resumed_at=?, stopped_at=NULL, updated_at=?, "
                "operational_code=NULL, operational_message=NULL, "
                "operational_recorded_at=NULL WHERE run_id=? AND status='stopped' "
                "AND phase IS NULL AND finished_at IS NULL AND stopped_at IS NOT NULL "
                "AND operational_code IS NULL AND operational_message IS NULL "
                "AND operational_recorded_at IS NULL",
                (
                    request.resumed_at,
                    request.resumed_at,
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("resume lost its state")

    def begin_publication_resume(self, request: PublicationResumeRequest) -> None:
        """Reactivate one exact failed publication without opening execution."""

        _validate_publication_resume_request(request)
        with self._transaction("publication_resume"):
            row = _sole_row(
                self._db,
                "SELECT p.run_id, p.stage, p.publication_identity, "
                "p.result_generation, p.failure_code, p.failure_message, "
                "p.failure_recorded_at, s.status, s.phase, s.finished_at, "
                "s.operational_code, s.operational_message, "
                "s.operational_recorded_at "
                "FROM publication_state p JOIN run_state s USING(run_id)",
            )
            if (
                row["stage"] != request.expected_stage
                or row["publication_identity"] != request.publication_identity
                or row["status"] != "failed"
                or row["phase"] is not None
                or row["finished_at"] is None
                or row["operational_code"] != "reproduction.publication.failed"
                or row["failure_code"] is None
                or row["failure_message"] is None
                or row["failure_recorded_at"] is None
                or row["operational_message"] != row["failure_message"]
                or row["operational_recorded_at"] != row["failure_recorded_at"]
                or (
                    request.expected_stage == "ready"
                    and row["result_generation"] is not None
                )
                or (
                    request.expected_stage == "result_committed"
                    and (
                        not isinstance(row["result_generation"], int)
                        or row["result_generation"] <= 0
                    )
                )
            ):
                raise JobStoreTransitionError("publication retry state changed")
            run_id = cast(str, row["run_id"])
            _require_publication_ready(self._db, run_id)
            changed = self._db.execute(
                "UPDATE run_state SET status=NULL, phase='publishing', "
                "resumed_at=?, finished_at=NULL, updated_at=?, "
                "operational_code=NULL, operational_message=NULL, "
                "operational_recorded_at=NULL WHERE run_id=? AND status='failed' "
                "AND phase IS NULL AND finished_at IS NOT NULL "
                "AND operational_code='reproduction.publication.failed'",
                (request.resumed_at, request.resumed_at, run_id),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("publication resume lost its state")

    def record_execution_terminal(self, terminal: ExecutionTerminal) -> None:
        """Atomically replace one active checkpoint with its terminal projection."""

        _validate_execution_terminal(terminal)
        _validate_workers(terminal.workers)
        if any(worker.state != "exited" for worker in terminal.workers):
            raise JobStoreInvariantError("terminal checkpoint retains a live worker")
        with self._transaction("terminal_checkpoint"):
            run_id, command_pk = _command_identity(
                self._db,
                terminal.entry,
                terminal.cid,
                terminal.execution_id,
                runnable=True,
            )
            row = self._db.execute(
                "SELECT state, permit_id, started_at, elapsed_seconds, scratch_path "
                "FROM execution_checkpoints "
                "WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            ).fetchone()
            if (
                row is None
                or row["state"] != terminal.expected_state
                or row["permit_id"] != terminal.permit_id
            ):
                raise JobStoreTransitionError(
                    "terminal checkpoint expected one permitted active execution"
                )
            if terminal.elapsed_seconds < float(row["elapsed_seconds"]):
                raise JobStoreTransitionError("terminal elapsed time moved backward")
            if terminal.state != "stopped" and (
                row["started_at"] is None or row["scratch_path"] is None
            ):
                raise JobStoreTransitionError(
                    "unlaunched execution cannot record a completed outcome"
                )
            if (
                terminal.finished_at is not None
                and row["started_at"] is not None
                and terminal.finished_at < row["started_at"]
            ):
                raise JobStoreTransitionError(
                    "execution finished before its first start"
                )
            _validate_terminal_output_ownership(self._db, run_id, command_pk, terminal)
            self._db.execute(
                "DELETE FROM checkpoint_outputs WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            )
            self._db.executemany(
                "INSERT INTO checkpoint_outputs VALUES (?, ?, ?, ?)",
                (
                    (
                        run_id,
                        command_pk,
                        output.artifact,
                        _canonical_json(output.fingerprint),
                    )
                    for output in terminal.outputs
                ),
            )
            changed = self._db.execute(
                "UPDATE execution_checkpoints SET state=?, checkpointed_at=?, "
                "finished_at=?, elapsed_seconds=?, failure_code=?, failure_message=?, "
                "failure_recorded_at=? WHERE run_id=? AND command_pk=? "
                "AND state='active' AND permit_id=?",
                (
                    terminal.state,
                    terminal.checkpointed_at,
                    terminal.finished_at,
                    terminal.elapsed_seconds,
                    terminal.failure_code,
                    terminal.failure_message,
                    terminal.failure_recorded_at,
                    run_id,
                    command_pk,
                    terminal.permit_id,
                ),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("terminal checkpoint lost its state")
            _validate_worker_replacement(self._db, run_id, command_pk, terminal.workers)
            _require_run_worker_capacity(
                self._db, run_id, command_pk, len(terminal.workers)
            )
            self._db.execute(
                "DELETE FROM workers WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            )
            self._db.executemany(
                "INSERT INTO workers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        run_id,
                        worker.worker_id,
                        worker.parent_worker_id,
                        command_pk,
                        worker.pid,
                        worker.state,
                        worker.registered_at,
                        worker.last_observed_at,
                    )
                    for worker in terminal.workers
                ),
            )
            completed_delta = int(terminal.state in {"succeeded", "failed"})
            if terminal.failure_code is None:
                changed = self._db.execute(
                    "UPDATE run_state SET "
                    "completed_executions=completed_executions+?, updated_at=? "
                    "WHERE run_id=? AND status IS NULL",
                    (completed_delta, terminal.checkpointed_at, run_id),
                ).rowcount
            else:
                changed = self._db.execute(
                    "UPDATE run_state SET "
                    "completed_executions=completed_executions+?, "
                    "latest_execution_entry=?, latest_execution_cid=?, "
                    "latest_execution_id=?, "
                    "latest_execution_code=?, latest_execution_message=?, "
                    "latest_execution_recorded_at=?, updated_at=? "
                    "WHERE run_id=? AND status IS NULL",
                    (
                        completed_delta,
                        terminal.entry,
                        terminal.cid,
                        terminal.execution_id,
                        terminal.failure_code,
                        terminal.failure_message,
                        terminal.failure_recorded_at,
                        terminal.checkpointed_at,
                        run_id,
                    ),
                ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("terminal run cannot accept a checkpoint")

    def replace_run_owner(self, owner: RunOwner) -> None:
        """Replace the one supervisor row without creating owner history."""

        _validate_run_owner(owner)
        with self._transaction("run_owner"):
            run_id = cast(str, _sole_row(self._db, "SELECT run_id FROM runs")[0])
            current = self._db.execute(
                "SELECT supervisor_pid, last_observed_at FROM run_owner WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if (
                current is not None
                and int(current["supervisor_pid"]) == owner.supervisor_pid
                and owner.last_observed_at < current["last_observed_at"]
            ):
                raise JobStoreTransitionError("owner observation moved backward")
            self._db.execute(
                "INSERT INTO run_owner VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "supervisor_pid=excluded.supervisor_pid, state=excluded.state, "
                "registered_at=excluded.registered_at, "
                "last_observed_at=excluded.last_observed_at",
                (
                    run_id,
                    owner.supervisor_pid,
                    owner.state,
                    owner.registered_at,
                    owner.last_observed_at,
                ),
            )

    def replace_execution_workers(
        self, identity: ExecutionIdentity, workers: Sequence[WorkerRecord]
    ) -> None:
        """Replace worker rows for one execution without touching siblings."""

        _require_execution_identity(identity.entry, identity.cid, identity.execution_id)
        _validate_workers(workers)
        with self._transaction("execution_workers"):
            run_id, command_pk = _command_identity(
                self._db,
                identity.entry,
                identity.cid,
                identity.execution_id,
                runnable=True,
            )
            checkpoint = self._db.execute(
                "SELECT state, permit_id, scratch_path FROM execution_checkpoints "
                "WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            ).fetchone()
            if (
                checkpoint is None
                or checkpoint["state"] != "active"
                or checkpoint["permit_id"] is None
                or checkpoint["scratch_path"] is None
            ):
                raise JobStoreTransitionError(
                    "worker replacement requires one launched active execution"
                )
            _require_run_worker_capacity(self._db, run_id, command_pk, len(workers))
            _validate_worker_replacement(self._db, run_id, command_pk, workers)
            self._db.execute(
                "DELETE FROM workers WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            )
            self._db.executemany(
                "INSERT INTO workers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        run_id,
                        worker.worker_id,
                        worker.parent_worker_id,
                        command_pk,
                        worker.pid,
                        worker.state,
                        worker.registered_at,
                        worker.last_observed_at,
                    )
                    for worker in workers
                ),
            )

    def replace_recovery_workers(
        self,
        observations: Sequence[RecoveryWorkerObservation],
        *,
        observed_at: str,
    ) -> None:
        """Persist the exhaustive live-process scan used by orphan recovery."""

        _require_timestamp(observed_at, "recovery observation time")
        workers = tuple(item.worker for item in observations)
        _validate_recovery_workers(workers)
        if any(worker.state != "running" for worker in workers):
            raise JobStoreInvariantError("recovery observation is not live")
        with self._transaction("recovery_workers"):
            state = _sole_row(self._db, "SELECT run_id, status FROM run_state")
            run_id = cast(str, state["run_id"])
            if observations and state["status"] is not None:
                raise JobStoreTransitionError(
                    "terminal run cannot retain recovery workers"
                )
            resolved = _resolve_recovery_workers(self._db, run_id, observations)
            _require_recovery_workers_unchanged(self._db, run_id, resolved)
            self._db.execute(
                "UPDATE workers SET state='exited', last_observed_at="
                "CASE WHEN last_observed_at>? THEN last_observed_at ELSE ? END "
                "WHERE run_id=? AND state='running'",
                (observed_at, observed_at, run_id),
            )
            for command_pk, worker in resolved:
                self._db.execute(
                    "INSERT INTO workers VALUES (?, ?, ?, ?, ?, 'running', ?, ?) "
                    "ON CONFLICT(run_id, worker_id) DO UPDATE SET "
                    "parent_worker_id=excluded.parent_worker_id, "
                    "command_pk=excluded.command_pk, state='running', "
                    "last_observed_at=excluded.last_observed_at",
                    (
                        run_id,
                        worker.worker_id,
                        worker.parent_worker_id,
                        command_pk,
                        worker.pid,
                        worker.registered_at,
                        worker.last_observed_at,
                    ),
                )
            _require_recovery_workers_present(self._db, run_id, resolved)

    def load_scheduler_owner(self) -> SchedulerOwnerProjection:
        """Read only current lifecycle, permits, owner, and running workers."""

        with self._snapshot():
            state = _sole_row(
                self._db,
                "SELECT r.run_id, s.status, s.phase, s.stop_requested_at "
                "FROM runs r JOIN run_state s USING(run_id)",
            )
            run_id = cast(str, state["run_id"])
            owner_row = self._db.execute(
                "SELECT supervisor_pid, state, registered_at, last_observed_at "
                "FROM run_owner WHERE run_id=?",
                (run_id,),
            ).fetchone()
            owner = (
                RunOwner(
                    int(owner_row["supervisor_pid"]),
                    cast(Literal["running", "stopped", "exited"], owner_row["state"]),
                    cast(str, owner_row["registered_at"]),
                    cast(str, owner_row["last_observed_at"]),
                )
                if owner_row is not None
                else None
            )
            checkpoint_rows = self._db.execute(
                "SELECT p.command_pk, p.state, p.permit_id, "
                "p.released_permit_id, p.checkpointed_at, p.started_at, "
                "p.finished_at, p.elapsed_seconds, p.failure_code, "
                "p.failure_message, p.failure_recorded_at, p.stdout_path, "
                "p.stderr_path, p.scratch_path, c.entry, c.cid, c.execution_id, "
                "e.plan_order "
                "FROM execution_checkpoints p "
                "JOIN accepted_commands c USING(run_id, command_pk) "
                "JOIN accepted_executions e USING(run_id, command_pk) "
                "WHERE p.run_id=? AND p.permit_id IS NOT NULL "
                "ORDER BY e.plan_order LIMIT 2049",
                (run_id,),
            ).fetchall()
            if len(checkpoint_rows) > 2_048:
                raise JobStoreInvariantError(
                    "permit checkpoint count crossed its bound"
                )
            checkpoints = tuple(
                _permit_checkpoint_projection(item) for item in checkpoint_rows
            )
            worker_rows = self._db.execute(
                "SELECT w.worker_id, w.parent_worker_id, w.command_pk, w.pid, "
                "w.registered_at, w.last_observed_at, c.entry, c.cid, c.execution_id "
                "FROM workers w "
                "LEFT JOIN accepted_commands c USING(run_id, command_pk) "
                "WHERE w.run_id=? AND w.state='running' "
                "ORDER BY COALESCE(c.entry, ''), COALESCE(c.execution_id, ''), "
                "w.worker_id LIMIT ?",
                (run_id, MAX_RUN_WORKERS + 1),
            ).fetchall()
            if len(worker_rows) > MAX_RUN_WORKERS:
                raise JobStoreInvariantError("running worker count crossed its bound")
            running = tuple(
                (
                    (
                        ExecutionIdentity(
                            cast(str, row["entry"]),
                            cast(str, row["cid"]),
                            cast(str, row["execution_id"]),
                        )
                        if row["command_pk"] is not None
                        else None
                    ),
                    WorkerRecord(
                        cast(str, row["worker_id"]),
                        cast(str | None, row["parent_worker_id"]),
                        int(row["pid"]),
                        "running",
                        cast(str, row["registered_at"]),
                        cast(str, row["last_observed_at"]),
                    ),
                )
                for row in worker_rows
            )
            projection = SchedulerOwnerProjection(
                run_id,
                cast(str | None, state["status"]),
                cast(str | None, state["phase"]),
                cast(str | None, state["stop_requested_at"]),
                owner,
                checkpoints,
                running,
            )
            _validate_scheduler_owner_projection(projection)
        return projection

    def load_run_owner(self) -> RunOwner | None:
        """Read the supervisor row without assuming lifecycle reconciliation."""

        with self._snapshot():
            run_id = cast(str, _sole_row(self._db, "SELECT run_id FROM runs")[0])
            owner_row = self._db.execute(
                "SELECT supervisor_pid, state, registered_at, last_observed_at "
                "FROM run_owner WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if owner_row is None:
            return None
        owner = RunOwner(
            int(owner_row["supervisor_pid"]),
            cast(Literal["running", "stopped", "exited"], owner_row["state"]),
            cast(str, owner_row["registered_at"]),
            cast(str, owner_row["last_observed_at"]),
        )
        _validate_run_owner(owner)
        return owner

    def record_execution_comparison(self, comparison: ExecutionComparisonWrite) -> None:
        """Commit only one execution's staging and comparison child rows."""

        _validate_comparison_write(comparison)
        with self._transaction("comparison"):
            run_id, command_pk = _command_identity(
                self._db,
                comparison.entry,
                comparison.cid,
                comparison.execution_id,
                runnable=True,
            )
            lifecycle = _sole_row(
                self._db,
                "SELECT status, phase, stop_requested_at, operational_code "
                "FROM run_state WHERE run_id=?",
                (run_id,),
            )
            if (
                lifecycle["status"] is not None
                or lifecycle["phase"] not in _PREPUBLICATION_PHASES
                or lifecycle["stop_requested_at"] is not None
                or lifecycle["operational_code"] is not None
            ):
                raise JobStoreTransitionError("run cannot accept comparison")
            checkpoint = self._db.execute(
                "SELECT state FROM execution_checkpoints "
                "WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            ).fetchone()
            accepted_artifacts = {
                cast(str, row["artifact"])
                for row in self._db.execute(
                    "SELECT artifact FROM accepted_command_outputs "
                    "WHERE run_id=? AND command_pk=?",
                    (run_id, command_pk),
                )
            }
            if {
                artifact.artifact for artifact in comparison.artifacts
            } != accepted_artifacts:
                raise JobStoreTransitionError(
                    "comparison does not cover every accepted output"
                )
            dependency_skip = _is_dependency_skip(comparison)
            if checkpoint is None and not dependency_skip:
                raise JobStoreTransitionError(
                    "comparison requires one same-run terminal checkpoint"
                )
            if checkpoint is None and not _has_failed_dependency(
                self._db, run_id, command_pk
            ):
                raise JobStoreTransitionError(
                    "dependency skip has no failed durable dependency"
                )
            if checkpoint is not None and (
                checkpoint["state"] not in {"succeeded", "failed"} or dependency_skip
            ):
                raise JobStoreTransitionError("comparison checkpoint state is invalid")
            for artifact in comparison.artifacts:
                _verify_accepted_comparison_input(
                    self._db, run_id, command_pk, artifact
                )
            prior_counts = _identity_outcome_counts(self._db, run_id, command_pk)
            prior_dependency_skip = _stored_dependency_skip(
                self._db, run_id, command_pk
            )
            self._db.execute(
                "DELETE FROM staged_executions WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            )
            self._db.execute(
                "INSERT INTO staged_executions VALUES (?, ?, ?, ?, ?)",
                (
                    run_id,
                    command_pk,
                    int(comparison.complete),
                    comparison.retained_bytes,
                    comparison.workspace_path,
                ),
            )
            self._db.executemany(
                "INSERT INTO staged_diagnostics VALUES (?, ?, ?, ?)",
                (
                    (run_id, command_pk, position, path)
                    for position, path in enumerate(comparison.diagnostics)
                ),
            )
            for artifact in comparison.artifacts:
                self._db.execute(
                    "INSERT INTO artifact_comparisons VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        command_pk,
                        artifact.artifact,
                        artifact.kind,
                        int(artifact.available),
                        artifact.staged_path,
                        artifact.outcome,
                        artifact.reason,
                        artifact.profile,
                        _optional_json(artifact.expected),
                        _optional_json(artifact.regenerated),
                        artifact.evidence_definition,
                    ),
                )
                self._db.executemany(
                    "INSERT INTO artifact_comparison_evidence VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        (
                            run_id,
                            command_pk,
                            artifact.artifact,
                            position,
                            evidence.record_id,
                            _canonical_json(evidence.retained),
                            _canonical_json(evidence.regenerated),
                            _canonical_json(evidence.tolerance),
                            int(evidence.matched),
                        )
                        for position, evidence in enumerate(artifact.evidence)
                    ),
                )
            self._db.execute(
                "INSERT INTO execution_effects VALUES (?, ?, ?, NULL) "
                "ON CONFLICT(run_id, command_pk) DO UPDATE SET "
                "comparison_recorded_at=excluded.comparison_recorded_at",
                (run_id, command_pk, comparison.recorded_at),
            )
            next_counts = _comparison_outcome_counts(comparison.artifacts)
            changed = self._db.execute(
                "UPDATE run_state SET phase='comparing', "
                "completed_executions=completed_executions+?, matched=matched+?, "
                "changed=changed+?, failed=failed+?, "
                "comparison_failed=comparison_failed+?, skipped=skipped+?, "
                "updated_at=? WHERE run_id=? AND status IS NULL "
                "AND phase IN ('accepted','planning','preflight','executing','comparing') "
                "AND stop_requested_at IS NULL "
                "AND operational_code IS NULL",
                (
                    int(dependency_skip) - int(prior_dependency_skip),
                    next_counts["matched"] - prior_counts["matched"],
                    next_counts["changed"] - prior_counts["changed"],
                    next_counts["failed"] - prior_counts["failed"],
                    next_counts["comparison_failed"]
                    - prior_counts["comparison_failed"],
                    next_counts["skipped"] - prior_counts["skipped"],
                    comparison.recorded_at,
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("terminal run cannot accept comparison")

    def record_requirement_effect(self, effect: RequirementEffect) -> None:
        """Compare-and-set one completed comparison's requirement checkpoint."""

        _require_execution_identity(effect.entry, effect.cid, effect.execution_id)
        _require_timestamp(effect.recorded_at, "requirement effect time")
        with self._transaction("requirement_effect"):
            run_id, command_pk = _command_identity(
                self._db, effect.entry, effect.cid, effect.execution_id, runnable=True
            )
            row = self._db.execute(
                "SELECT e.comparison_recorded_at, e.requirement_cleared_at, "
                "c.accepted_requires_reproduction, p.state AS checkpoint_state "
                "FROM execution_effects e "
                "JOIN accepted_commands c USING(run_id, command_pk) "
                "JOIN execution_checkpoints p USING(run_id, command_pk) "
                "WHERE e.run_id=? AND e.command_pk=?",
                (run_id, command_pk),
            ).fetchone()
            if row is None or row["comparison_recorded_at"] is None:
                raise JobStoreTransitionError(
                    "requirement effect requires a durable comparison"
                )
            if row["checkpoint_state"] != "succeeded" or not bool(
                row["accepted_requires_reproduction"]
            ):
                raise JobStoreTransitionError(
                    "accepted execution has no reproduction requirement"
                )
            if row["requirement_cleared_at"] is not None:
                if row["requirement_cleared_at"] == effect.recorded_at:
                    return
                raise JobStoreTransitionError("requirement effect is already recorded")
            changed = self._db.execute(
                "UPDATE execution_effects SET requirement_cleared_at=? "
                "WHERE run_id=? AND command_pk=? AND requirement_cleared_at IS NULL",
                (effect.recorded_at, run_id, command_pk),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError("requirement effect lost its state")
            self._db.execute(
                "UPDATE run_state SET updated_at=? WHERE run_id=?",
                (effect.recorded_at, run_id),
            )

    def load_publication_projection(self) -> PublicationProjection:
        """Read the immutable plan and terminal publication inputs in one snapshot."""

        with self._snapshot():
            run = _sole_row(
                self._db,
                "SELECT r.run_id, r.run_path, r.accepted_at, s.status, s.finished_at "
                "FROM runs r JOIN run_state s USING(run_id)",
            )
            plan = _load_accepted_plan(self._db)
            status = _load_run_status(self._db)
            publication = _load_publication_state(self._db, cast(str, run["run_id"]))
            comparisons = _load_comparison_writes(self._db, plan)
        return PublicationProjection(
            RunIdentity(cast(str, run["run_id"]), cast(str, run["run_path"])),
            plan,
            cast(str, run["accepted_at"]),
            cast(str | None, run["finished_at"]),
            cast(str | None, run["status"]),
            status.checkpoints,
            comparisons,
            publication,
        )

    def prepare_publication(
        self, publication_identity: str, *, updated_at: str
    ) -> None:
        """Set the immutable publication identity and make the run retry-ready."""

        _require_publication_identity(publication_identity)
        _require_timestamp(updated_at, "publication update time")
        with self._transaction("publication_state"):
            row = _sole_row(
                self._db,
                "SELECT p.run_id, p.stage, s.status, s.phase, "
                "s.stop_requested_at, s.operational_code FROM publication_state p "
                "JOIN run_state s USING(run_id)",
            )
            if (
                row["stage"] != "not_ready"
                or row["status"] is not None
                or row["phase"] == "stopping"
                or row["stop_requested_at"] is not None
                or row["operational_code"] is not None
            ):
                raise JobStoreTransitionError("job is not ready to prepare publication")
            run_id = cast(str, row["run_id"])
            _require_publication_ready(self._db, run_id)
            self._db.execute(
                "UPDATE publication_state SET stage='ready', "
                "publication_identity=?, updated_at=? "
                "WHERE run_id=? AND stage='not_ready'",
                (publication_identity, updated_at, run_id),
            )
            self._db.execute(
                "UPDATE run_state SET phase='publishing', updated_at=? "
                "WHERE run_id=? AND status IS NULL AND phase<>'stopping' "
                "AND stop_requested_at IS NULL AND operational_code IS NULL",
                (updated_at, run_id),
            )

    def begin_publication(self, publication_identity: str, *, updated_at: str) -> None:
        """Move a matching ready publication to the in-flight state."""

        _require_publication_identity(publication_identity)
        _require_timestamp(updated_at, "publication update time")
        self._advance_publication(
            "ready",
            "publishing",
            publication_identity,
            updated_at,
            operation="publication_state",
        )

    def record_result_commit(
        self,
        publication_identity: str,
        generation: int,
        *,
        updated_at: str,
    ) -> None:
        """Record one committed result generation for the in-flight identity."""

        _require_positive_generation(generation)
        _require_timestamp(updated_at, "publication update time")
        with self._transaction("publication_state"):
            run_id = _matching_publication(self._db, "publishing", publication_identity)
            self._db.execute(
                "UPDATE publication_state SET stage='result_committed', "
                "result_generation=?, failure_code=NULL, failure_message=NULL, "
                "failure_recorded_at=NULL, updated_at=? WHERE run_id=?",
                (generation, updated_at, run_id),
            )

    def reset_absent_publication(
        self, publication_identity: str, *, updated_at: str
    ) -> None:
        """Return an interrupted absent result commit to its durable retry point."""

        _require_publication_identity(publication_identity)
        _require_timestamp(updated_at, "publication update time")
        self._advance_publication(
            "publishing",
            "ready",
            publication_identity,
            updated_at,
            operation="publication_state",
        )

    def reset_missing_result_commit(
        self, publication_identity: str, *, updated_at: str
    ) -> None:
        """Clear a vanished disposable result checkpoint for normal recreation."""

        _require_publication_identity(publication_identity)
        _require_timestamp(updated_at, "publication update time")
        with self._transaction("publication_state"):
            run_id = _matching_publication(
                self._db, "result_committed", publication_identity
            )
            changed = self._db.execute(
                "UPDATE publication_state SET stage='ready', "
                "result_generation=NULL, report_generation=NULL, "
                "failure_code=NULL, failure_message=NULL, failure_recorded_at=NULL, "
                "updated_at=? WHERE run_id=? AND stage='result_committed'",
                (updated_at, run_id),
            ).rowcount
            if changed != 1:
                raise JobStoreTransitionError(
                    "missing result reset lost its publication state"
                )

    def record_report_commit(
        self,
        publication_identity: str,
        generation: int,
        *,
        updated_at: str,
    ) -> None:
        """Record report materialization and terminally complete the job."""

        _require_positive_generation(generation)
        _require_timestamp(updated_at, "publication update time")
        with self._transaction("publication_state"):
            run_id = _matching_publication(
                self._db, "result_committed", publication_identity
            )
            self._db.execute(
                "UPDATE publication_state SET stage='complete', report_generation=?, "
                "failure_code=NULL, failure_message=NULL, failure_recorded_at=NULL, "
                "updated_at=? WHERE run_id=?",
                (generation, updated_at, run_id),
            )
            self._db.execute(
                "UPDATE run_state SET status='complete', phase=NULL, finished_at=?, "
                "updated_at=?, operational_code=NULL, operational_message=NULL, "
                "operational_recorded_at=NULL WHERE run_id=?",
                (updated_at, updated_at, run_id),
            )

    def record_publication_failure(
        self, expected_stage: str, failure: PublicationFailure
    ) -> None:
        """Return a failed result commit to ready or retain report-only retry."""

        if expected_stage not in {"publishing", "result_committed"}:
            raise JobStoreInvariantError("publication failure stage is invalid")
        _require_timestamp(failure.recorded_at, "publication failure time")
        if not _bounded_string(failure.code) or not _bounded_string(failure.message):
            raise JobStoreInvariantError("publication failure is incomplete")
        target = "ready" if expected_stage == "publishing" else "result_committed"
        with self._transaction("publication_state"):
            row = _sole_row(
                self._db,
                "SELECT p.run_id, p.stage, s.status, s.phase, "
                "s.stop_requested_at, s.operational_code FROM publication_state p "
                "JOIN run_state s USING(run_id)",
            )
            if (
                row["stage"] != expected_stage
                or row["status"] is not None
                or row["phase"] != "publishing"
                or row["stop_requested_at"] is not None
                or row["operational_code"] is not None
            ):
                raise JobStoreTransitionError(
                    "publication failure did not match its expected stage"
                )
            self._db.execute(
                "UPDATE publication_state SET stage=?, failure_code=?, "
                "failure_message=?, failure_recorded_at=?, updated_at=? "
                "WHERE run_id=? AND stage=?",
                (
                    target,
                    failure.code,
                    failure.message,
                    failure.recorded_at,
                    failure.recorded_at,
                    row["run_id"],
                    expected_stage,
                ),
            )
            self._db.execute(
                "UPDATE run_state SET status='failed', phase=NULL, finished_at=?, "
                "updated_at=?, "
                "operational_code=?, operational_message=?, "
                "operational_recorded_at=? WHERE run_id=?",
                (
                    failure.recorded_at,
                    failure.recorded_at,
                    "reproduction.publication.failed",
                    failure.message,
                    failure.recorded_at,
                    row["run_id"],
                ),
            )

    def audit_job_state(self) -> JobAudit:
        """Scan every row and validate complete-store lifecycle invariants."""

        with self._snapshot():
            integrity = cast(
                str, self._db.execute("PRAGMA integrity_check").fetchone()[0]
            )
            foreign_keys = self._db.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != "ok":
                raise JobStoreMalformedError(f"SQLite integrity check: {integrity}")
            if foreign_keys:
                raise JobStoreInvariantError("durable job has foreign-key violations")
            plan = _load_accepted_plan(self._db)
            status = _load_run_status(self._db)
            publication = _load_publication_state(self._db, status.run_id)
            comparisons = _load_comparison_writes(self._db, plan)
            _audit_lifecycle(self._db, plan, status, publication)
            _audit_mutable_rows(self._db, status, comparisons)
            tables = tuple(
                cast(str, row["name"])
                for row in self._db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
            counts = {
                table: int(
                    self._db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                )
                for table in tables
            }
        return JobAudit(
            status.run_id,
            counts,
            integrity,
            len(foreign_keys),
            _store_size(self.path),
        )

    def _advance_publication(
        self,
        expected: str,
        target: str,
        publication_identity: str,
        updated_at: str,
        *,
        operation: str,
    ) -> None:
        with self._transaction(operation):
            row = _sole_row(
                self._db,
                "SELECT p.run_id, p.stage, p.publication_identity, s.status, "
                "s.phase, s.stop_requested_at, s.operational_code "
                "FROM publication_state p JOIN run_state s USING(run_id)",
            )
            if row["stage"] != expected:
                raise JobStoreTransitionError(
                    f"publication expected {expected}, found {row['stage']}"
                )
            existing = row["publication_identity"]
            if existing is not None and existing != publication_identity:
                raise JobStoreTransitionError("publication identity changed")
            if (
                row["status"] is not None
                or row["phase"] == "stopping"
                or row["stop_requested_at"] is not None
                or row["operational_code"] is not None
            ):
                raise JobStoreTransitionError("run cannot advance publication")
            self._db.execute(
                "UPDATE publication_state SET stage=?, publication_identity=?, "
                "failure_code=NULL, failure_message=NULL, failure_recorded_at=NULL, "
                "updated_at=? WHERE run_id=? AND stage=?",
                (
                    target,
                    publication_identity,
                    updated_at,
                    row["run_id"],
                    expected,
                ),
            )
            self._db.execute(
                "UPDATE run_state SET phase='publishing', updated_at=? "
                "WHERE run_id=? AND status IS NULL AND phase<>'stopping' "
                "AND stop_requested_at IS NULL AND operational_code IS NULL",
                (updated_at, row["run_id"]),
            )

    @contextmanager
    def _snapshot(self) -> Iterator[None]:
        try:
            self._db.execute("BEGIN")
            yield
            self._db.commit()
        except BaseException as error:
            self._db.rollback()
            _raise_storage_error(error)

    @contextmanager
    def _transaction(self, operation: str) -> Iterator[None]:
        try:
            self._db.execute("BEGIN IMMEDIATE")
            yield
            _check_database_size(self._db)
            _before_commit(operation, self._db)
            self._db.commit()
        except BaseException as error:
            self._db.rollback()
            _raise_storage_error(error)


def _insert_accepted_job(db: sqlite3.Connection, accepted: AcceptedJob) -> None:
    plan = accepted.plan
    target_kind = cast(str, plan.target["kind"])
    target_entry = cast(str | None, plan.target["entry"])
    db.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            accepted.run_id,
            plan.summary,
            target_kind,
            target_entry,
            int(plan.include_all),
            plan.jobs,
            plan.execution_timeout_seconds,
            accepted.accepted_at,
            accepted.run_path,
            accepted.workspace_path,
            accepted.diagnostics_path,
        ),
    )
    db.execute(
        "INSERT INTO run_state VALUES "
        "(?, NULL, 'accepted', NULL, NULL, NULL, NULL, NULL, ?, 0, 0, 0, 0, 0, 0, "
        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
        (accepted.run_id, accepted.accepted_at),
    )
    db.execute(
        "INSERT INTO publication_state VALUES "
        "(?, 'not_ready', NULL, NULL, NULL, NULL, NULL, NULL, ?)",
        (accepted.run_id, accepted.accepted_at),
    )
    _insert_admission(db, accepted.run_id, plan.admission)
    command_keys = _insert_commands(db, accepted.run_id, plan.commands)
    material_keys = _insert_materials_and_command_links(
        db, accepted.run_id, plan, command_keys
    )
    _insert_executions(db, accepted.run_id, plan.executions, command_keys)
    _insert_cases_boundaries_failures(db, accepted.run_id, plan, command_keys)
    _insert_comparisons(db, accepted.run_id, plan, command_keys)
    _insert_evidence_context(db, accepted.run_id, plan, material_keys)


def _insert_admission(
    db: sqlite3.Connection, run_id: str, admission: Mapping[str, object]
) -> None:
    db.execute(
        "INSERT INTO accepted_admission VALUES (?, ?, ?, ?, ?)",
        (
            run_id,
            admission["validation_id"],
            admission["validation_result_id"],
            admission["rules_version"],
            admission["evaluated_at"],
        ),
    )
    batch = cast(Mapping[str, object], admission["batch_admission"])
    for disposition in ("admitted", "excluded"):
        decisions = cast(Sequence[Mapping[str, object]], batch[disposition])
        for position, decision in enumerate(decisions):
            group_id = decision.get("chain_id", decision.get("group_id"))
            if not isinstance(group_id, str):
                raise JobStoreInvariantError("admission decision has no group identity")
            db.execute(
                "INSERT INTO accepted_admission_groups VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    disposition,
                    position,
                    decision["entry"],
                    group_id,
                    _canonical_json(decision),
                ),
            )


def _insert_commands(
    db: sqlite3.Connection,
    run_id: str,
    commands: Sequence[Mapping[str, object]],
) -> dict[tuple[str, str, str], int]:
    keys: dict[tuple[str, str, str], int] = {}
    for command_pk, command in enumerate(commands, 1):
        _validate_command_snapshot(command)
        state = cast(Mapping[str, object], command["execution_state"])
        recipe = cast(Mapping[str, object], state["recipe"])
        key = (
            cast(str, command["entry"]),
            cast(str, command["cid"]),
            cast(str, command["execution_id"]),
        )
        keys[key] = command_pk
        db.execute(
            "INSERT INTO accepted_commands VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                command_pk,
                key[0],
                key[1],
                key[2],
                command["selection"],
                int(cast(bool, command["auto_reproduce"])),
                int(cast(bool, command["exclusive"])),
                int(cast(bool, command["queued"])),
                int(cast(bool, command["requires_reproduction"])),
                command["prior_disposition"],
                command["source_digest"],
                command["entry_root"],
                command["project_root"],
                command["cwd"],
                recipe["script"],
                state["last_run_at"],
                state["runner"],
                state["environment_profile"],
                state["execution_contract"],
                _canonical_json(command["details"]),
                (
                    _canonical_json(command["data_declaration"])
                    if command["data_declaration"] is not None
                    else None
                ),
            ),
        )
        db.executemany(
            "INSERT INTO accepted_recipe_parameters VALUES (?, ?, ?, ?)",
            (
                (run_id, command_pk, position, value)
                for position, value in enumerate(
                    cast(Sequence[str], recipe["parameters"])
                )
            ),
        )
        roles = cast(Mapping[str, str], recipe["parameter_roles"])
        db.executemany(
            "INSERT INTO accepted_parameter_roles VALUES (?, ?, ?, ?)",
            (
                (run_id, command_pk, key_name, roles[key_name])
                for key_name in sorted(roles)
            ),
        )
        environment = cast(Mapping[str, str], recipe["environment"])
        db.executemany(
            "INSERT INTO accepted_recipe_environment VALUES (?, ?, ?, ?)",
            (
                (run_id, command_pk, name, environment[name])
                for name in sorted(environment)
            ),
        )
    return keys


def _insert_materials_and_command_links(
    db: sqlite3.Connection,
    run_id: str,
    plan: ReproductionPlan,
    command_keys: Mapping[tuple[str, str, str], int],
) -> dict[tuple[str, str, str], int]:
    materials = cast(
        Sequence[Mapping[str, object]], plan.comparison_context["materials"]
    )
    material_keys: dict[tuple[str, str, str], int] = {}
    next_pk = 1

    def retain(
        role: str,
        identity: str,
        kind: str,
        fingerprint: object,
        selection: object = None,
    ) -> int:
        nonlocal next_pk
        fingerprint_json = _canonical_json(fingerprint)
        key = (role, identity, fingerprint_json)
        existing = material_keys.get(key)
        selection_json = _canonical_json(selection) if selection is not None else None
        if existing is not None:
            row = db.execute(
                "SELECT kind, selection_json FROM accepted_materials "
                "WHERE run_id=? AND material_pk=?",
                (run_id, existing),
            ).fetchone()
            if (
                row is None
                or row["kind"] != kind
                or row["selection_json"] != selection_json
            ):
                raise JobStoreInvariantError("accepted material identity is ambiguous")
            return existing
        material_pk = next_pk
        next_pk += 1
        db.execute(
            "INSERT INTO accepted_materials VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                material_pk,
                role,
                identity,
                kind,
                selection_json,
                fingerprint_json,
            ),
        )
        material_keys[key] = material_pk
        return material_pk

    for position, material in enumerate(materials):
        material_pk = retain(
            cast(str, material["role"]),
            cast(str, material["identity"]),
            cast(str, material["kind"]),
            material["fingerprint"],
            material.get("selection"),
        )
        db.execute(
            "INSERT INTO accepted_comparison_materials VALUES (?, ?, ?)",
            (run_id, position, material_pk),
        )

    for command in plan.commands:
        command_pk = command_keys[
            (
                cast(str, command["entry"]),
                cast(str, command["cid"]),
                cast(str, command["execution_id"]),
            )
        ]
        state = cast(Mapping[str, object], command["execution_state"])
        recipe = cast(Mapping[str, object], state["recipe"])
        observed = cast(Mapping[str, object], state["observed"])
        entry_root = Path(cast(str, command["entry_root"]))
        project_root = Path(cast(str, command["project_root"]))
        declarations = _declaration_index(command.get("data_declaration"))
        input_observations = cast(Mapping[str, object], observed["inputs"])
        for name in cast(Sequence[str], recipe["inputs"]):
            declaration = declarations.get(name)
            if declaration is None:
                raise JobStoreInvariantError("accepted command input is undeclared")
            identity = cast(str, declaration["canonical_target"])
            material_pk = retain(
                "input",
                identity,
                cast(str, declaration["kind"]),
                input_observations[name],
                declaration["identity"],
            )
            db.execute(
                "INSERT INTO accepted_command_inputs VALUES (?, ?, ?, ?)",
                (run_id, command_pk, name, material_pk),
            )
        output_observations = cast(Mapping[str, object], observed["outputs"])
        outputs = cast(Mapping[str, str], recipe["outputs"])
        for artifact in sorted(outputs):
            identity = (
                output_target_path(
                    artifact, entry_root=entry_root, project_root=project_root
                )
                .resolve()
                .as_posix()
            )
            material_pk = retain(
                "comparison_baseline",
                identity,
                outputs[artifact],
                output_observations[artifact],
            )
            db.execute(
                "INSERT INTO accepted_command_outputs VALUES (?, ?, ?, ?, ?)",
                (run_id, command_pk, artifact, outputs[artifact], material_pk),
            )
        script = cast(str, recipe["script"])
        script_identity = (
            script_target_path(script, entry_root=entry_root, project_root=project_root)
            .resolve()
            .as_posix()
        )
        script_pk = retain("script", script_identity, "file", observed["script"])
        db.execute(
            "INSERT INTO accepted_command_code VALUES (?, ?, ?, 'script', ?)",
            (run_id, command_pk, script, script_pk),
        )
        code = cast(Mapping[str, object], observed["code"])
        for path in sorted(code):
            identity = (
                code_target_path(path, entry_root=entry_root).resolve().as_posix()
            )
            code_pk = retain("code", identity, "file", code[path])
            db.execute(
                "INSERT INTO accepted_command_code VALUES (?, ?, ?, 'code', ?)",
                (run_id, command_pk, path, code_pk),
            )
    return material_keys


def _insert_executions(
    db: sqlite3.Connection,
    run_id: str,
    executions: Sequence[Mapping[str, object]],
    command_keys: Mapping[tuple[str, str, str], int],
) -> None:
    for execution in executions:
        key = (
            cast(str, execution["entry"]),
            cast(str, execution["cid"]),
            cast(str, execution["execution_id"]),
        )
        command_pk = command_keys[key]
        db.execute(
            "INSERT INTO accepted_executions VALUES (?, ?, ?, ?)",
            (run_id, command_pk, execution["order"], execution["run_path"]),
        )
        for position, dependency in enumerate(
            cast(Sequence[str], execution["depends_on"])
        ):
            dependency_entry, dependency_cid, dependency_id = dependency.split(":", 2)
            dependency_key = (dependency_entry, dependency_cid, dependency_id)
            db.execute(
                "INSERT INTO accepted_execution_dependencies VALUES (?, ?, ?, ?)",
                (run_id, command_pk, command_keys[dependency_key], position),
            )
        for position, artifact in enumerate(cast(Sequence[str], execution["outputs"])):
            db.execute(
                "INSERT INTO accepted_execution_outputs VALUES (?, ?, ?, ?)",
                (run_id, command_pk, position, artifact),
            )
        for source, kind in (
            ("read_paths", "read"),
            ("write_paths", "write"),
            ("writable_paths", "writable"),
        ):
            for position, path in enumerate(cast(Sequence[str], execution[source])):
                db.execute(
                    "INSERT INTO accepted_execution_claims VALUES (?, ?, ?, ?, ?)",
                    (run_id, command_pk, kind, position, path),
                )


def _insert_cases_boundaries_failures(
    db: sqlite3.Connection,
    run_id: str,
    plan: ReproductionPlan,
    command_keys: Mapping[tuple[str, str, str], int],
) -> None:
    for position, case in enumerate(plan.cases):
        execution_id = case.get("execution_id")
        command_pk = (
            command_keys.get(
                (
                    cast(str, case["entry"]),
                    cast(str, case["cid"]),
                    execution_id,
                )
            )
            if isinstance(execution_id, str)
            else None
        )
        db.execute(
            "INSERT INTO accepted_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                position,
                case["entry"],
                case["artifact"],
                command_pk,
                case["disposition"],
                case["reason"],
            ),
        )
    for position, boundary in enumerate(plan.boundaries):
        db.execute(
            "INSERT INTO accepted_boundaries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                position,
                boundary["kind"],
                boundary.get("entry"),
                boundary.get("name"),
                boundary.get("artifact"),
                (
                    _canonical_json(boundary["selection"])
                    if "selection" in boundary
                    else None
                ),
                _canonical_json(boundary["fingerprint"]),
            ),
        )
    for position, failure in enumerate(plan.failures):
        db.execute(
            "INSERT INTO accepted_failures VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                position,
                failure["entry"],
                failure["artifact"],
                failure["outcome"],
                failure["reason"],
                _canonical_json(failure["dependencies"]),
            ),
        )


def _insert_comparisons(
    db: sqlite3.Connection,
    run_id: str,
    plan: ReproductionPlan,
    command_keys: Mapping[tuple[str, str, str], int],
) -> None:
    comparisons = cast(
        Sequence[Mapping[str, object]], plan.comparison_context["comparisons"]
    )
    for comparison in comparisons:
        command_pk = command_keys[
            (
                cast(str, comparison["entry"]),
                cast(str, comparison["cid"]),
                cast(str, comparison["execution_id"]),
            )
        ]
        artifact = cast(str, comparison["output"])
        db.execute(
            "INSERT INTO accepted_comparisons VALUES (?, ?, ?, ?)",
            (run_id, command_pk, artifact, comparison["definition_identity"]),
        )
        for position, record in enumerate(
            cast(Sequence[Mapping[str, object]], comparison["evidence_records"])
        ):
            db.execute(
                "INSERT INTO accepted_comparison_records VALUES (?, ?, ?, ?, ?)",
                (run_id, command_pk, artifact, position, _canonical_json(record)),
            )


def _insert_evidence_context(
    db: sqlite3.Connection,
    run_id: str,
    plan: ReproductionPlan,
    _material_keys: Mapping[tuple[str, str, str], int],
) -> None:
    values = cast(
        Sequence[Mapping[str, object]], plan.comparison_context["evidence_only"]
    )
    for observation_pk, value in enumerate(values, 1):
        db.execute(
            "INSERT INTO accepted_evidence_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                observation_pk,
                value["entry"],
                value["record_id"],
                value["resource"],
                value["kind"],
                _canonical_json(value["selection"]),
                _canonical_json(value["fingerprint"]),
            ),
        )
        db.executemany(
            "INSERT INTO accepted_evidence_comparison_links VALUES (?, ?, ?)",
            (
                (run_id, observation_pk, identity)
                for identity in cast(Sequence[str], value["comparisons"])
            ),
        )


def _load_accepted_plan(db: sqlite3.Connection) -> ReproductionPlan:
    run = _sole_row(db, "SELECT * FROM runs")
    run_id = cast(str, run["run_id"])
    commands, identities = _load_commands(db, run_id)
    admission = _load_admission(db, run_id)
    context = _load_comparison_context(db, run_id, identities)
    executions = _load_executions(db, run_id, identities)
    cases = _load_cases(db, run_id, identities)
    boundaries = _load_boundaries(db, run_id)
    failures = _load_failures(db, run_id)
    target = {"entry": run["target_entry"], "kind": run["target_kind"]}
    plan = ReproductionPlan(
        cast(str, run["summary"]),
        target,
        bool(run["include_all"]),
        admission,
        commands,
        context,
        cases,
        executions,
        boundaries,
        failures,
        int(run["jobs"]),
        int(run["execution_timeout_seconds"]),
    )
    try:
        raw = plan.serialized().encode("utf-8")
        return ReproductionPlan.from_json(raw)
    except ValueError as error:
        raise JobStoreInvariantError(str(error)) from error


def _load_commands(
    db: sqlite3.Connection, run_id: str
) -> tuple[tuple[Mapping[str, object], ...], dict[int, tuple[str, str, str]]]:
    commands: list[Mapping[str, object]] = []
    identities: dict[int, tuple[str, str, str]] = {}
    rows = db.execute(
        "SELECT * FROM accepted_commands WHERE run_id=? ORDER BY command_pk", (run_id,)
    ).fetchall()
    for row in rows:
        command_pk = int(row["command_pk"])
        key = (
            cast(str, row["entry"]),
            cast(str, row["cid"]),
            cast(str, row["execution_id"]),
        )
        identities[command_pk] = key
        parameters = [
            item["value"]
            for item in db.execute(
                "SELECT value FROM accepted_recipe_parameters "
                "WHERE run_id=? AND command_pk=? ORDER BY position",
                (run_id, command_pk),
            )
        ]
        roles = {
            item["selector"]: item["role"]
            for item in db.execute(
                "SELECT selector, role FROM accepted_parameter_roles "
                "WHERE run_id=? AND command_pk=? ORDER BY selector",
                (run_id, command_pk),
            )
        }
        environment = {
            item["name"]: item["value"]
            for item in db.execute(
                "SELECT name, value FROM accepted_recipe_environment "
                "WHERE run_id=? AND command_pk=? ORDER BY name",
                (run_id, command_pk),
            )
        }
        input_rows = db.execute(
            "SELECT i.name, m.fingerprint_json FROM accepted_command_inputs i "
            "JOIN accepted_materials m USING(run_id, material_pk) "
            "WHERE i.run_id=? AND i.command_pk=? ORDER BY i.name",
            (run_id, command_pk),
        ).fetchall()
        output_rows = db.execute(
            "SELECT o.artifact, o.kind, m.fingerprint_json FROM accepted_command_outputs o "
            "JOIN accepted_materials m USING(run_id, material_pk) "
            "WHERE o.run_id=? AND o.command_pk=? ORDER BY o.artifact",
            (run_id, command_pk),
        ).fetchall()
        code_rows = db.execute(
            "SELECT c.path, c.role, m.fingerprint_json FROM accepted_command_code c "
            "JOIN accepted_materials m USING(run_id, material_pk) "
            "WHERE c.run_id=? AND c.command_pk=? ORDER BY c.role DESC, c.path",
            (run_id, command_pk),
        ).fetchall()
        script_rows = [item for item in code_rows if item["role"] == "script"]
        if len(script_rows) != 1 or script_rows[0]["path"] != row["script"]:
            raise JobStoreInvariantError(
                "accepted command script observation is invalid"
            )
        execution_state = {
            "auto_reproduce": bool(row["auto_reproduce"]),
            "environment_profile": row["environment_profile"],
            "exclusive": bool(row["exclusive"]),
            "execution_contract": row["execution_contract"],
            "last_run_at": row["last_run_at"],
            "observed": {
                "code": {
                    item["path"]: _decode_json(item["fingerprint_json"], dict)
                    for item in code_rows
                    if item["role"] == "code"
                },
                "inputs": {
                    item["name"]: _decode_json(item["fingerprint_json"], dict)
                    for item in input_rows
                },
                "outputs": {
                    item["artifact"]: _decode_json(item["fingerprint_json"], dict)
                    for item in output_rows
                },
                "script": _decode_json(script_rows[0]["fingerprint_json"], dict),
            },
            "recipe": {
                "environment": environment,
                "inputs": [item["name"] for item in input_rows],
                "outputs": {item["artifact"]: item["kind"] for item in output_rows},
                "parameter_roles": roles,
                "parameters": parameters,
                "script": row["script"],
            },
            "requires_reproduction": bool(row["accepted_requires_reproduction"]),
            "runner": row["runner"],
        }
        command = {
            "auto_reproduce": bool(row["auto_reproduce"]),
            "cwd": row["cwd"],
            "data_declaration": (
                _decode_json(row["data_declaration_json"], dict)
                if row["data_declaration_json"] is not None
                else None
            ),
            "details": _decode_json(row["details_json"], list),
            "entry": key[0],
            "cid": key[1],
            "entry_root": row["entry_root"],
            "exclusive": bool(row["exclusive"]),
            "execution_id": key[2],
            "execution_state": execution_state,
            "prior_disposition": row["prior_disposition"],
            "project_root": row["project_root"],
            "queued": bool(row["queued"]),
            "requires_reproduction": bool(row["accepted_requires_reproduction"]),
            "selection": row["selection"],
            "source_digest": row["source_digest"],
        }
        _validate_command_snapshot(command)
        commands.append(command)
    return tuple(commands), identities


def _load_admission(db: sqlite3.Connection, run_id: str) -> Mapping[str, object]:
    row = _sole_row(db, "SELECT * FROM accepted_admission WHERE run_id=?", (run_id,))
    groups: dict[str, list[object]] = {"admitted": [], "excluded": []}
    for group in db.execute(
        "SELECT disposition, position, decision_json FROM accepted_admission_groups "
        "WHERE run_id=? ORDER BY disposition, position",
        (run_id,),
    ):
        groups[cast(str, group["disposition"])].append(
            _decode_json(group["decision_json"], dict)
        )
    return {
        "batch_admission": {
            "admitted": groups["admitted"],
            "excluded": groups["excluded"],
            "schema": "research-log-reproduction-batch-admission/2",
        },
        "evaluated_at": row["evaluated_at"],
        "rules_version": row["rules_version"],
        "validation_id": row["validation_id"],
        "validation_result_id": row["validation_result_id"],
    }


def _load_comparison_context(
    db: sqlite3.Connection,
    run_id: str,
    identities: Mapping[int, tuple[str, str, str]],
) -> Mapping[str, object]:
    materials = []
    for row in db.execute(
        "SELECT m.* FROM accepted_comparison_materials c "
        "JOIN accepted_materials m USING(run_id, material_pk) "
        "WHERE c.run_id=? ORDER BY c.position",
        (run_id,),
    ):
        value: dict[str, object] = {
            "fingerprint": _decode_json(row["fingerprint_json"], dict),
            "identity": row["identity"],
            "kind": row["kind"],
            "role": row["role"],
        }
        if row["selection_json"] is not None:
            value["selection"] = _decode_json(row["selection_json"], dict)
        materials.append(value)
    comparisons = []
    for row in db.execute(
        "SELECT * FROM accepted_comparisons WHERE run_id=? ORDER BY command_pk, artifact",
        (run_id,),
    ):
        command_pk = int(row["command_pk"])
        if command_pk not in identities:
            raise JobStoreInvariantError("accepted comparison command is missing")
        records = [
            _decode_json(item["record_json"], dict)
            for item in db.execute(
                "SELECT record_json FROM accepted_comparison_records "
                "WHERE run_id=? AND command_pk=? AND artifact=? ORDER BY position",
                (run_id, command_pk, row["artifact"]),
            )
        ]
        comparisons.append(
            {
                "definition_identity": row["definition_identity"],
                "entry": identities[command_pk][0],
                "cid": identities[command_pk][1],
                "evidence_records": records,
                "execution_id": identities[command_pk][2],
                "output": row["artifact"],
            }
        )
    evidence_only = []
    for row in db.execute(
        "SELECT * FROM accepted_evidence_observations WHERE run_id=? ORDER BY observation_pk",
        (run_id,),
    ):
        links = [
            item["definition_identity"]
            for item in db.execute(
                "SELECT definition_identity FROM accepted_evidence_comparison_links "
                "WHERE run_id=? AND observation_pk=? ORDER BY definition_identity",
                (run_id, row["observation_pk"]),
            )
        ]
        evidence_only.append(
            {
                "comparisons": links,
                "entry": row["entry"],
                "fingerprint": _decode_json(row["fingerprint_json"], dict),
                "kind": row["kind"],
                "record_id": row["record_id"],
                "resource": row["resource"],
                "selection": _decode_json(row["selection_json"], dict),
            }
        )
    return {
        "comparisons": comparisons,
        "evidence_only": evidence_only,
        "materials": materials,
        "result_schema": "research-log-reproduction-result/11",
        "schema": "research-log-reproduction-comparison-context/1",
    }


def _load_executions(
    db: sqlite3.Connection,
    run_id: str,
    identities: Mapping[int, tuple[str, str, str]],
) -> tuple[Mapping[str, object], ...]:
    executions = []
    for row in db.execute(
        "SELECT e.*, c.auto_reproduce, c.exclusive FROM accepted_executions e "
        "JOIN accepted_commands c USING(run_id, command_pk) "
        "WHERE e.run_id=? ORDER BY e.plan_order",
        (run_id,),
    ):
        command_pk = int(row["command_pk"])
        entry, cid, execution_id = identities[command_pk]
        dependencies = []
        for item in db.execute(
            "SELECT dependency_command_pk FROM accepted_execution_dependencies "
            "WHERE run_id=? AND command_pk=? ORDER BY position",
            (run_id, command_pk),
        ):
            dependency = identities[int(item["dependency_command_pk"])]
            dependencies.append(f"{dependency[0]}:{dependency[1]}:{dependency[2]}")
        claims: dict[str, list[object]] = {
            "read": [],
            "write": [],
            "writable": [],
        }
        for item in db.execute(
            "SELECT claim_kind, path FROM accepted_execution_claims "
            "WHERE run_id=? AND command_pk=? ORDER BY claim_kind, position",
            (run_id, command_pk),
        ):
            claims[cast(str, item["claim_kind"])].append(item["path"])
        executions.append(
            {
                "auto_reproduce": bool(row["auto_reproduce"]),
                "depends_on": dependencies,
                "entry": entry,
                "cid": cid,
                "exclusive": bool(row["exclusive"]),
                "execution_id": execution_id,
                "order": int(row["plan_order"]),
                "outputs": [
                    item["artifact"]
                    for item in db.execute(
                        "SELECT artifact FROM accepted_execution_outputs "
                        "WHERE run_id=? AND command_pk=? ORDER BY output_command_position",
                        (run_id, command_pk),
                    )
                ],
                "read_paths": claims["read"],
                "run_path": row["run_path"],
                "writable_paths": claims["writable"],
                "write_paths": claims["write"],
            }
        )
    return tuple(executions)


def _load_cases(
    db: sqlite3.Connection,
    run_id: str,
    identities: Mapping[int, tuple[str, str, str]],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "artifact": row["artifact"],
            "disposition": row["disposition"],
            "entry": row["entry"],
            "cid": (
                identities[int(row["command_pk"])][1]
                if row["command_pk"] is not None
                else None
            ),
            "execution_id": (
                identities[int(row["command_pk"])][2]
                if row["command_pk"] is not None
                else None
            ),
            "reason": row["reason"],
        }
        for row in db.execute(
            "SELECT * FROM accepted_cases WHERE run_id=? ORDER BY position", (run_id,)
        )
    )


def _load_boundaries(
    db: sqlite3.Connection, run_id: str
) -> tuple[Mapping[str, object], ...]:
    values = []
    for row in db.execute(
        "SELECT * FROM accepted_boundaries WHERE run_id=? ORDER BY position", (run_id,)
    ):
        value: dict[str, object] = {
            "artifact": row["artifact"],
            "entry": row["entry"],
            "fingerprint": _decode_json(row["fingerprint_json"], dict),
            "kind": row["kind"],
            "name": row["name"],
        }
        if row["selection_json"] is not None:
            value["selection"] = _decode_json(row["selection_json"], dict)
        values.append(value)
    return tuple(values)


def _load_failures(
    db: sqlite3.Connection, run_id: str
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "artifact": row["artifact"],
            "dependencies": _decode_json(row["dependencies_json"], list),
            "entry": row["entry"],
            "outcome": row["outcome"],
            "reason": row["reason"],
        }
        for row in db.execute(
            "SELECT * FROM accepted_failures WHERE run_id=? ORDER BY position",
            (run_id,),
        )
    )


def _load_run_status(db: sqlite3.Connection) -> RunStatus:
    row = _sole_row(
        db,
        "SELECT r.*, s.* FROM runs r JOIN run_state s USING(run_id)",
    )
    run_id = cast(str, row["run_id"])
    checkpoint_rows = db.execute(
        "SELECT p.*, c.entry, c.cid, c.execution_id, e.plan_order "
        "FROM execution_checkpoints p "
        "JOIN accepted_commands c USING(run_id, command_pk) "
        "JOIN accepted_executions e USING(run_id, command_pk) "
        "WHERE p.run_id=? ORDER BY e.plan_order",
        (run_id,),
    ).fetchall()
    checkpoints = tuple(
        _checkpoint_projection(db, run_id, item) for item in checkpoint_rows
    )
    workers = tuple(
        (
            (
                ExecutionIdentity(
                    cast(str, item["entry"]),
                    cast(str, item["cid"]),
                    cast(str, item["execution_id"]),
                )
                if item["command_pk"] is not None
                else None
            ),
            WorkerRecord(
                cast(str, item["worker_id"]),
                cast(str | None, item["parent_worker_id"]),
                int(item["pid"]),
                cast(Literal["running", "exited"], item["state"]),
                cast(str, item["registered_at"]),
                cast(str, item["last_observed_at"]),
            ),
        )
        for item in db.execute(
            "SELECT w.*, c.entry, c.cid, c.execution_id FROM workers w "
            "LEFT JOIN accepted_commands c USING(run_id, command_pk) "
            "WHERE w.run_id=? ORDER BY COALESCE(c.entry, ''), "
            "COALESCE(c.execution_id, ''), w.worker_id",
            (run_id,),
        )
    )
    latest = (
        DiagnosticProjection(
            cast(str, row["latest_execution_code"]),
            cast(str, row["latest_execution_message"]),
            cast(str, row["latest_execution_recorded_at"]),
            cast(str, row["latest_execution_entry"]),
            cast(str, row["latest_execution_cid"]),
            cast(str, row["latest_execution_id"]),
        )
        if row["latest_execution_code"] is not None
        else None
    )
    operational = (
        DiagnosticProjection(
            cast(str, row["operational_code"]),
            cast(str, row["operational_message"]),
            cast(str, row["operational_recorded_at"]),
        )
        if row["operational_code"] is not None
        else None
    )
    status = RunStatus(
        run_id,
        cast(str, row["summary"]),
        {"entry": row["target_entry"], "kind": row["target_kind"]},
        bool(row["include_all"]),
        int(row["jobs"]),
        int(row["execution_timeout_seconds"]),
        cast(str, row["accepted_at"]),
        cast(str, row["run_path"]),
        cast(str | None, row["status"]),
        cast(str | None, row["phase"]),
        cast(str | None, row["stop_requested_at"]),
        cast(str | None, row["started_at"]),
        cast(str | None, row["resumed_at"]),
        cast(str | None, row["stopped_at"]),
        cast(str | None, row["finished_at"]),
        cast(str, row["updated_at"]),
        int(row["completed_executions"]),
        {
            "changed": int(row["changed"]),
            "comparison_failed": int(row["comparison_failed"]),
            "failed": int(row["failed"]),
            "matched": int(row["matched"]),
            "skipped": int(row["skipped"]),
        },
        latest,
        operational,
        checkpoints,
        workers,
        int(
            db.execute(
                "SELECT COUNT(*) FROM accepted_executions WHERE run_id=?", (run_id,)
            ).fetchone()[0]
        ),
    )
    _validate_run_status_projection(status)
    return status


def _checkpoint_projection(
    db: sqlite3.Connection, run_id: str, item: sqlite3.Row
) -> CheckpointProjection:
    outputs = tuple(
        CheckpointOutput(
            cast(str, output["artifact"]),
            _decode_fingerprint(
                output["fingerprint_json"],
                cast(str, output["kind"]),
                f"checkpoint output {output['artifact']}",
            ),
        )
        for output in db.execute(
            "SELECT p.artifact, p.fingerprint_json, o.kind "
            "FROM checkpoint_outputs p JOIN accepted_command_outputs o "
            "USING(run_id, command_pk, artifact) "
            "WHERE p.run_id=? AND p.command_pk=? ORDER BY p.artifact",
            (run_id, item["command_pk"]),
        )
    )
    return CheckpointProjection(
        cast(str, item["entry"]),
        cast(str, item["cid"]),
        cast(str, item["execution_id"]),
        cast(str, item["state"]),
        cast(str | None, item["permit_id"]),
        cast(str | None, item["released_permit_id"]),
        cast(str, item["checkpointed_at"]),
        cast(str | None, item["started_at"]),
        cast(str | None, item["finished_at"]),
        float(item["elapsed_seconds"]),
        cast(str | None, item["failure_code"]),
        cast(str | None, item["failure_message"]),
        cast(str | None, item["failure_recorded_at"]),
        cast(str | None, item["stdout_path"]),
        cast(str | None, item["stderr_path"]),
        cast(str | None, item["scratch_path"]),
        outputs,
    )


def _permit_checkpoint_projection(item: sqlite3.Row) -> CheckpointProjection:
    """Build a scheduler checkpoint without reading terminal output history."""

    return CheckpointProjection(
        cast(str, item["entry"]),
        cast(str, item["cid"]),
        cast(str, item["execution_id"]),
        cast(str, item["state"]),
        cast(str | None, item["permit_id"]),
        cast(str | None, item["released_permit_id"]),
        cast(str, item["checkpointed_at"]),
        cast(str | None, item["started_at"]),
        cast(str | None, item["finished_at"]),
        float(item["elapsed_seconds"]),
        cast(str | None, item["failure_code"]),
        cast(str | None, item["failure_message"]),
        cast(str | None, item["failure_recorded_at"]),
        cast(str | None, item["stdout_path"]),
        cast(str | None, item["stderr_path"]),
        cast(str | None, item["scratch_path"]),
        (),
    )


def _load_publication_state(
    db: sqlite3.Connection, run_id: str
) -> PublicationStateProjection:
    row = _sole_row(db, "SELECT * FROM publication_state WHERE run_id=?", (run_id,))
    stage = cast(str, row["stage"])
    if stage not in _PUBLICATION_STAGES:
        raise JobStoreInvariantError("publication stage is invalid")
    projection = PublicationStateProjection(
        stage,
        cast(str | None, row["publication_identity"]),
        cast(int | None, row["result_generation"]),
        cast(int | None, row["report_generation"]),
        cast(str | None, row["failure_code"]),
        cast(str | None, row["failure_message"]),
        cast(str | None, row["failure_recorded_at"]),
        cast(str, row["updated_at"]),
    )
    _validate_publication_projection(projection)
    return projection


def _validate_publication_projection(
    publication: PublicationStateProjection,
) -> None:
    if publication.publication_identity is not None:
        _require_publication_identity(publication.publication_identity)
    for generation in (
        publication.result_generation,
        publication.report_generation,
    ):
        if generation is not None:
            _require_positive_generation(generation)
    failure = (
        publication.failure_code,
        publication.failure_message,
        publication.failure_recorded_at,
    )
    if any(value is not None for value in failure) and not all(
        _bounded_string(value) for value in failure
    ):
        raise JobStoreInvariantError("publication failure is incomplete")
    if publication.failure_recorded_at is not None:
        _require_timestamp(publication.failure_recorded_at, "publication failure time")
    _require_timestamp(publication.updated_at, "publication update time")


def _load_comparison_writes(
    db: sqlite3.Connection, plan: ReproductionPlan
) -> tuple[ExecutionComparisonWrite, ...]:
    run_id = cast(str, _sole_row(db, "SELECT run_id FROM runs")[0])
    command_rows = db.execute(
        "SELECT s.*, c.entry, c.cid, c.execution_id, e.plan_order "
        "FROM staged_executions s JOIN accepted_commands c USING(run_id, command_pk) "
        "JOIN accepted_executions e USING(run_id, command_pk) "
        "WHERE s.run_id=? ORDER BY e.plan_order",
        (run_id,),
    ).fetchall()
    values = []
    for command in command_rows:
        command_pk = int(command["command_pk"])
        diagnostics = tuple(
            cast(str, row["path"])
            for row in db.execute(
                "SELECT path FROM staged_diagnostics WHERE run_id=? AND command_pk=? "
                "ORDER BY position",
                (run_id, command_pk),
            )
        )
        artifacts = []
        for row in db.execute(
            "SELECT a.*, m.fingerprint_json AS accepted_fingerprint, "
            "c.definition_identity AS accepted_definition "
            "FROM artifact_comparisons a "
            "JOIN accepted_command_outputs o USING(run_id, command_pk, artifact) "
            "JOIN accepted_materials m ON m.run_id=o.run_id AND m.material_pk=o.material_pk "
            "LEFT JOIN accepted_comparisons c USING(run_id, command_pk, artifact) "
            "WHERE a.run_id=? AND a.command_pk=? ORDER BY a.artifact",
            (run_id, command_pk),
        ):
            evidence = tuple(
                ComparisonEvidence(
                    cast(str, item["record_id"]),
                    cast(
                        Mapping[str, object], _decode_json(item["retained_json"], dict)
                    ),
                    cast(
                        Mapping[str, object],
                        _decode_json(item["regenerated_json"], dict),
                    ),
                    cast(
                        Mapping[str, object], _decode_json(item["tolerance_json"], dict)
                    ),
                    bool(item["matched"]),
                )
                for item in db.execute(
                    "SELECT * FROM artifact_comparison_evidence "
                    "WHERE run_id=? AND command_pk=? AND artifact=? ORDER BY position",
                    (run_id, command_pk, row["artifact"]),
                )
            )
            artifacts.append(
                ArtifactComparisonWrite(
                    cast(str, row["artifact"]),
                    cast(str, row["kind"]),
                    bool(row["available"]),
                    cast(str | None, row["staged_path"]),
                    cast(
                        Literal[
                            "matched",
                            "changed",
                            "failed",
                            "comparison_failed",
                            "skipped",
                        ],
                        row["outcome"],
                    ),
                    cast(str | None, row["reason"]),
                    cast(str | None, row["profile"]),
                    (
                        cast(
                            Mapping[str, object],
                            _decode_json(row["expected_json"], dict),
                        )
                        if row["expected_json"] is not None
                        else None
                    ),
                    (
                        cast(
                            Mapping[str, object],
                            _decode_json(row["regenerated_json"], dict),
                        )
                        if row["regenerated_json"] is not None
                        else None
                    ),
                    cast(str | None, row["evidence_definition"]),
                    cast(
                        Mapping[str, object],
                        _decode_json(row["accepted_fingerprint"], dict),
                    ),
                    cast(str | None, row["accepted_definition"]),
                    evidence,
                )
            )
        effect = db.execute(
            "SELECT comparison_recorded_at FROM execution_effects "
            "WHERE run_id=? AND command_pk=?",
            (run_id, command_pk),
        ).fetchone()
        if effect is None or effect["comparison_recorded_at"] is None:
            raise JobStoreInvariantError("staged execution has no comparison effect")
        values.append(
            ExecutionComparisonWrite(
                cast(str, command["entry"]),
                cast(str, command["cid"]),
                cast(str, command["execution_id"]),
                bool(command["complete"]),
                int(command["retained_bytes"]),
                cast(str, command["workspace_path"]),
                diagnostics,
                tuple(artifacts),
                cast(str, effect["comparison_recorded_at"]),
            )
        )
    return tuple(values)


def _validate_run_owner(owner: RunOwner) -> None:
    if (
        isinstance(owner.supervisor_pid, bool)
        or owner.supervisor_pid <= 0
        or owner.state not in {"running", "stopped", "exited"}
    ):
        raise JobStoreInvariantError("run owner is invalid")
    _require_timestamp(owner.registered_at, "owner registration time")
    _require_timestamp(owner.last_observed_at, "owner observation time")
    if owner.last_observed_at < owner.registered_at:
        raise JobStoreInvariantError("owner observation precedes registration")


def _validate_workers(workers: Sequence[WorkerRecord]) -> None:
    _validate_worker_forest(workers, MAX_EXECUTION_WORKERS, "execution")


def _validate_recovery_workers(workers: Sequence[WorkerRecord]) -> None:
    """Validate the bounded run-wide process scan used for durable exclusion."""

    _validate_worker_forest(workers, MAX_RUN_WORKERS, "run")


def _validate_worker_forest(
    workers: Sequence[WorkerRecord], limit: int, scope: str
) -> None:
    if len(workers) > limit:
        raise JobStoreInvariantError(f"{scope} worker count crossed its bound")
    identities = [item.worker_id for item in workers]
    if len(identities) != len(set(identities)):
        raise JobStoreInvariantError("worker identity is duplicated")
    known = set(identities)
    for worker in workers:
        _validate_worker_record(worker)
        if worker.parent_worker_id is not None and worker.parent_worker_id not in known:
            raise JobStoreInvariantError("worker record is invalid")
    parents = {worker.worker_id: worker.parent_worker_id for worker in workers}
    for worker_id in identities:
        visited: set[str] = set()
        current: str | None = worker_id
        while current is not None:
            if current in visited:
                raise JobStoreInvariantError("worker parent relationship is cyclic")
            visited.add(current)
            current = parents[current]


def _resolve_recovery_workers(
    db: sqlite3.Connection,
    run_id: str,
    observations: Sequence[RecoveryWorkerObservation],
) -> tuple[tuple[int | None, WorkerRecord], ...]:
    resolved: list[tuple[int | None, WorkerRecord]] = []
    for observation in observations:
        command_pk: int | None = None
        if observation.identity is not None:
            _require_execution_identity(
                observation.identity.entry,
                observation.identity.cid,
                observation.identity.execution_id,
            )
            _run_id, command_pk = _command_identity(
                db,
                observation.identity.entry,
                observation.identity.cid,
                observation.identity.execution_id,
                runnable=True,
            )
            checkpoint = db.execute(
                "SELECT state FROM execution_checkpoints "
                "WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            ).fetchone()
            if checkpoint is None or checkpoint["state"] != "active":
                command_pk = None
        resolved.append((command_pk, observation.worker))
    return tuple(resolved)


def _require_recovery_workers_unchanged(
    db: sqlite3.Connection,
    run_id: str,
    resolved: Sequence[tuple[int | None, WorkerRecord]],
) -> None:
    existing = {
        cast(str, row["worker_id"]): row
        for row in db.execute(
            "SELECT worker_id, pid, registered_at, last_observed_at "
            "FROM workers WHERE run_id=?",
            (run_id,),
        )
    }
    for _command_pk, worker in resolved:
        prior = existing.get(worker.worker_id)
        if prior is not None and (
            int(prior["pid"]) != worker.pid
            or worker.last_observed_at < prior["last_observed_at"]
        ):
            raise JobStoreTransitionError("recovery worker identity fields changed")


def _require_recovery_workers_present(
    db: sqlite3.Connection,
    run_id: str,
    resolved: Sequence[tuple[int | None, WorkerRecord]],
) -> None:
    retained_ids = {worker.worker_id for _command_pk, worker in resolved}
    if not retained_ids:
        return
    placeholders = ",".join("?" for _item in retained_ids)
    count = int(
        db.execute(
            f"SELECT COUNT(*) FROM workers WHERE run_id=? "
            f"AND worker_id IN ({placeholders}) AND state='running'",
            (run_id, *sorted(retained_ids)),
        ).fetchone()[0]
    )
    if count != len(retained_ids):
        raise JobStoreTransitionError("recovery worker replacement lost its identity")


def _validate_worker_record(worker: WorkerRecord) -> None:
    if (
        not _bounded_string(worker.worker_id)
        or isinstance(worker.pid, bool)
        or worker.pid <= 0
        or worker.state not in {"running", "exited"}
        or worker.parent_worker_id == worker.worker_id
        or (
            worker.parent_worker_id is not None
            and not _bounded_string(worker.parent_worker_id)
        )
    ):
        raise JobStoreInvariantError("worker record is invalid")
    _require_timestamp(worker.registered_at, "worker registration time")
    _require_timestamp(worker.last_observed_at, "worker observation time")
    if worker.last_observed_at < worker.registered_at:
        raise JobStoreInvariantError("worker observation precedes registration")


def _validate_scheduler_owner_projection(
    projection: SchedulerOwnerProjection,
) -> None:
    _validate_scheduler_lifecycle(projection)
    _validate_scheduler_ownership(projection)


def _validate_scheduler_lifecycle(projection: SchedulerOwnerProjection) -> None:
    if _RUN_ID_RE.fullmatch(projection.run_id) is None:
        raise JobStoreInvariantError("scheduler owner run ID is invalid")
    if projection.status not in {None, "complete", "stopped", "failed"}:
        raise JobStoreInvariantError("scheduler owner lifecycle is invalid")
    phases = {
        None,
        "accepted",
        "planning",
        "preflight",
        "executing",
        "comparing",
        "publishing",
        "stopping",
    }
    if projection.phase not in phases or (
        (projection.status is None) == (projection.phase is None)
    ):
        raise JobStoreInvariantError("scheduler owner lifecycle is invalid")
    _require_optional_timestamp(projection.stop_requested_at, "stop request time")
    if projection.stop_requested_at is not None and (
        projection.status is None and projection.phase != "stopping"
    ):
        raise JobStoreInvariantError("scheduler stop request has an invalid phase")


def _validate_scheduler_ownership(projection: SchedulerOwnerProjection) -> None:
    if projection.owner is not None:
        _validate_run_owner(projection.owner)
        if projection.status is not None and projection.owner.state == "running":
            raise JobStoreInvariantError("terminal run retains a running owner")
    for checkpoint in projection.checkpoints:
        _validate_checkpoint_projection(checkpoint)
        if checkpoint.permit_id is None:
            raise JobStoreInvariantError("scheduler checkpoint has no current permit")
        if projection.status is not None:
            raise JobStoreInvariantError("terminal run retains an attached permit")
    for identity, worker in projection.running_workers:
        if identity is not None:
            _require_execution_identity(
                identity.entry, identity.cid, identity.execution_id
            )
        _validate_worker_record(worker)
        if worker.state != "running":
            raise JobStoreInvariantError("scheduler worker is not live")
    if projection.running_workers and (
        projection.owner is None or projection.owner.state != "running"
    ):
        raise JobStoreInvariantError("live workers have no running owner")


def _validate_worker_replacement(
    db: sqlite3.Connection,
    run_id: str,
    command_pk: int,
    workers: Sequence[WorkerRecord],
) -> None:
    requested = {worker.worker_id: worker for worker in workers}
    existing = db.execute(
        "SELECT worker_id, parent_worker_id, pid, state, registered_at, "
        "last_observed_at FROM workers WHERE run_id=? AND command_pk=?",
        (run_id, command_pk),
    ).fetchall()
    for row in existing:
        current = requested.get(cast(str, row["worker_id"]))
        if current is None:
            raise JobStoreTransitionError("worker replacement omitted durable history")
        immutable = (
            row["parent_worker_id"],
            int(row["pid"]),
            row["registered_at"],
        )
        if immutable != (
            current.parent_worker_id,
            current.pid,
            current.registered_at,
        ):
            raise JobStoreTransitionError("worker identity fields changed")
        if current.last_observed_at < row["last_observed_at"]:
            raise JobStoreTransitionError("worker observation moved backward")
        if row["state"] == "exited" and current.state != "exited":
            raise JobStoreTransitionError("exited worker cannot become running")


def _require_run_worker_capacity(
    db: sqlite3.Connection,
    run_id: str,
    replaced_command_pk: int,
    replacement_count: int,
) -> None:
    other_count = int(
        db.execute(
            "SELECT COUNT(*) FROM workers "
            "WHERE run_id=? AND (command_pk IS NULL OR command_pk<>?)",
            (run_id, replaced_command_pk),
        ).fetchone()[0]
    )
    if other_count + replacement_count > MAX_RUN_WORKERS:
        raise JobStoreInvariantError("run worker count crossed its bound")


def _validate_comparison_write(comparison: ExecutionComparisonWrite) -> None:
    _require_execution_identity(
        comparison.entry, comparison.cid, comparison.execution_id
    )
    if not isinstance(comparison.complete, bool):
        raise JobStoreInvariantError("comparison completeness is invalid")
    if (
        isinstance(comparison.retained_bytes, bool)
        or not isinstance(comparison.retained_bytes, int)
        or comparison.retained_bytes < 0
    ):
        raise JobStoreInvariantError("retained comparison bytes are invalid")
    _require_relative_path(comparison.workspace_path, "comparison workspace path")
    for path in comparison.diagnostics:
        _require_relative_path(path, "comparison diagnostic path")
    _require_timestamp(comparison.recorded_at, "comparison time")
    artifacts = [item.artifact for item in comparison.artifacts]
    if len(artifacts) != len(set(artifacts)):
        raise JobStoreInvariantError("comparison artifact is duplicated")
    for artifact in comparison.artifacts:
        _validate_comparison_artifact(artifact)


def _validate_comparison_artifact(artifact: ArtifactComparisonWrite) -> None:
    if not isinstance(artifact.available, bool):
        raise JobStoreInvariantError("comparison artifact availability is invalid")
    if not _bounded_string(artifact.artifact) or artifact.kind not in {
        "file",
        "directory",
    }:
        raise JobStoreInvariantError("comparison artifact identity is invalid")
    if artifact.outcome not in _empty_outcome_counts():
        raise JobStoreInvariantError("comparison artifact outcome is invalid")
    if artifact.staged_path is not None:
        _require_relative_path(artifact.staged_path, "staged artifact path")
    if (artifact.outcome == "matched") != (artifact.reason is None):
        raise JobStoreInvariantError("comparison artifact reason is inconsistent")
    if artifact.reason is not None and not _bounded_string(artifact.reason):
        raise JobStoreInvariantError("comparison artifact reason is invalid")
    if artifact.available != (artifact.staged_path is not None):
        raise JobStoreInvariantError("comparison artifact availability is inconsistent")
    _validate_comparison_fingerprints(artifact)
    _validate_comparison_evidence(artifact.evidence)


def _validate_comparison_fingerprints(artifact: ArtifactComparisonWrite) -> None:
    _validate_fingerprint(
        artifact.accepted_baseline,
        artifact.kind,
        f"accepted comparison baseline {artifact.artifact}",
    )
    for label, value in (
        ("expected", artifact.expected),
        ("regenerated", artifact.regenerated),
    ):
        if value is not None:
            _validate_fingerprint(
                value,
                artifact.kind,
                f"comparison {label} {artifact.artifact}",
            )


def _validate_comparison_evidence(evidence: Sequence[ComparisonEvidence]) -> None:
    evidence_ids = [item.record_id for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise JobStoreInvariantError("comparison evidence is duplicated")
    for item in evidence:
        if (
            not isinstance(item.matched, bool)
            or not _bounded_string(item.record_id)
            or not all(
                isinstance(value, Mapping)
                for value in (
                    item.retained,
                    item.regenerated,
                    item.tolerance,
                )
            )
        ):
            raise JobStoreInvariantError("comparison evidence is invalid")


def _identity_outcome_counts(
    db: sqlite3.Connection, run_id: str, command_pk: int
) -> dict[str, int]:
    counts = _empty_outcome_counts()
    for row in db.execute(
        "SELECT outcome, COUNT(*) AS count FROM artifact_comparisons "
        "WHERE run_id=? AND command_pk=? GROUP BY outcome",
        (run_id, command_pk),
    ):
        counts[cast(str, row["outcome"])] = int(row["count"])
    return counts


def _comparison_outcome_counts(
    artifacts: Sequence[ArtifactComparisonWrite],
) -> dict[str, int]:
    counts = _empty_outcome_counts()
    for artifact in artifacts:
        counts[artifact.outcome] += 1
    return counts


def _empty_outcome_counts() -> dict[str, int]:
    return {
        "matched": 0,
        "changed": 0,
        "failed": 0,
        "comparison_failed": 0,
        "skipped": 0,
    }


def _verify_accepted_comparison_input(
    db: sqlite3.Connection,
    run_id: str,
    command_pk: int,
    artifact: ArtifactComparisonWrite,
) -> None:
    row = db.execute(
        "SELECT o.kind, m.fingerprint_json, c.definition_identity "
        "FROM accepted_command_outputs o "
        "JOIN accepted_materials m USING(run_id, material_pk) "
        "LEFT JOIN accepted_comparisons c USING(run_id, command_pk, artifact) "
        "WHERE o.run_id=? AND o.command_pk=? AND o.artifact=?",
        (run_id, command_pk, artifact.artifact),
    ).fetchone()
    evidence_definition_mismatch = (
        artifact.evidence_definition is not None
        and artifact.evidence_definition != artifact.accepted_definition
    )
    if (
        row is None
        or row["kind"] != artifact.kind
        or row["fingerprint_json"] != _canonical_json(artifact.accepted_baseline)
        or row["definition_identity"] != artifact.accepted_definition
        or evidence_definition_mismatch
    ):
        raise JobStoreInvariantError("comparison accepted inputs do not match")


def _is_dependency_skip(comparison: ExecutionComparisonWrite) -> bool:
    return (
        not comparison.complete
        and bool(comparison.artifacts)
        and all(
            artifact.outcome == "skipped"
            and artifact.reason == "dependency_failed"
            and not artifact.available
            and artifact.staged_path is None
            and artifact.regenerated is None
            and artifact.profile is None
            and artifact.evidence_definition is None
            and not artifact.evidence
            for artifact in comparison.artifacts
        )
    )


def _stored_dependency_skip(
    db: sqlite3.Connection, run_id: str, command_pk: int
) -> bool:
    row = db.execute(
        "SELECT s.complete, COUNT(a.artifact) AS artifacts, "
        "SUM(a.outcome='skipped' AND a.reason='dependency_failed' "
        "AND a.available=0 AND a.staged_path IS NULL "
        "AND a.regenerated_json IS NULL AND a.profile IS NULL "
        "AND a.evidence_definition IS NULL) AS skipped "
        "FROM staged_executions s LEFT JOIN artifact_comparisons a "
        "USING(run_id, command_pk) WHERE s.run_id=? AND s.command_pk=? "
        "GROUP BY s.run_id, s.command_pk",
        (run_id, command_pk),
    ).fetchone()
    return bool(
        row is not None
        and not bool(row["complete"])
        and int(row["artifacts"]) > 0
        and int(row["artifacts"]) == int(row["skipped"])
    )


def _has_failed_dependency(
    db: sqlite3.Connection, run_id: str, command_pk: int
) -> bool:
    dependencies = tuple(
        int(row["dependency_command_pk"])
        for row in db.execute(
            "SELECT dependency_command_pk FROM accepted_execution_dependencies "
            "WHERE run_id=? AND command_pk=? ORDER BY position",
            (run_id, command_pk),
        )
    )
    for dependency_pk in dependencies:
        checkpoint = db.execute(
            "SELECT state FROM execution_checkpoints WHERE run_id=? AND command_pk=?",
            (run_id, dependency_pk),
        ).fetchone()
        if checkpoint is not None and checkpoint["state"] == "failed":
            return True
        if checkpoint is None and _stored_dependency_skip(db, run_id, dependency_pk):
            return True
    return False


def _require_publication_identity(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise JobStoreInvariantError("publication identity is invalid")


def _validate_publication_resume_request(request: PublicationResumeRequest) -> None:
    _require_publication_identity(request.publication_identity)
    if request.expected_stage not in {"ready", "result_committed"}:
        raise JobStoreInvariantError("publication resume stage is invalid")
    _require_timestamp(request.resumed_at, "publication resume time")


def _require_positive_generation(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise JobStoreInvariantError("publication generation is invalid")


def _matching_publication(
    db: sqlite3.Connection, stage: str, publication_identity: str
) -> str:
    _require_publication_identity(publication_identity)
    row = _sole_row(
        db,
        "SELECT p.run_id, p.stage, p.publication_identity, s.status, s.phase, "
        "s.stop_requested_at, s.operational_code FROM publication_state p "
        "JOIN run_state s USING(run_id)",
    )
    if (
        row["stage"] != stage
        or row["publication_identity"] != publication_identity
        or row["status"] is not None
        or row["phase"] != "publishing"
        or row["stop_requested_at"] is not None
        or row["operational_code"] is not None
    ):
        raise JobStoreTransitionError("publication state or identity changed")
    return cast(str, row["run_id"])


def _require_publication_ready(db: sqlite3.Connection, run_id: str) -> None:
    _require_quiescent_run(db, run_id)
    expected = int(
        db.execute(
            "SELECT COUNT(*) FROM accepted_executions WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    )
    terminal = int(
        db.execute(
            "SELECT COUNT(*) FROM execution_checkpoints WHERE run_id=? "
            "AND state IN ('succeeded', 'failed')",
            (run_id,),
        ).fetchone()[0]
    ) + len(_dependency_skip_command_pks(db, run_id))
    staged = int(
        db.execute(
            "SELECT COUNT(*) FROM staged_executions WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    )
    compared = int(
        db.execute(
            "SELECT COUNT(*) FROM execution_effects WHERE run_id=? "
            "AND comparison_recorded_at IS NOT NULL",
            (run_id,),
        ).fetchone()[0]
    )
    uncleared = int(
        db.execute(
            "SELECT COUNT(*) FROM accepted_executions x "
            "JOIN accepted_commands c USING(run_id, command_pk) "
            "JOIN execution_checkpoints p USING(run_id, command_pk) "
            "LEFT JOIN execution_effects e USING(run_id, command_pk) "
            "WHERE x.run_id=? AND p.state='succeeded' "
            "AND c.accepted_requires_reproduction=1 "
            "AND (e.requirement_cleared_at IS NULL)",
            (run_id,),
        ).fetchone()[0]
    )
    if expected != terminal or expected != staged or expected != compared or uncleared:
        raise JobStoreTransitionError("job has incomplete terminal comparisons")
    status = _load_run_status(db)
    _audit_comparison_counts(db, status)


def _audit_lifecycle(
    db: sqlite3.Connection,
    plan: ReproductionPlan,
    status: RunStatus,
    publication: PublicationStateProjection,
) -> None:
    _audit_execution_lifecycle(db, plan, status)
    _audit_comparison_counts(db, status)
    _audit_publication_state(publication)
    if (status.status == "complete") != (publication.stage == "complete"):
        raise JobStoreInvariantError("run and publication completion disagree")
    if status.status is None and publication.stage != "not_ready":
        if status.phase != "publishing":
            raise JobStoreInvariantError("active publication has an invalid run phase")
    if status.status == "stopped" and publication.stage != "not_ready":
        raise JobStoreInvariantError("stopped run retains publication state")
    if status.status == "failed":
        publication_failure = (
            status.operational_failure is not None
            and status.operational_failure.code == "reproduction.publication.failed"
        )
        retry_stage = publication.stage in {"ready", "result_committed"}
        retry_failure = publication.failure_code is not None
        if publication_failure != (retry_stage and retry_failure):
            raise JobStoreInvariantError("failed run publication state is inconsistent")
        if publication_failure and (
            status.operational_failure is None
            or status.operational_failure.message != publication.failure_message
            or status.operational_failure.recorded_at != publication.failure_recorded_at
        ):
            raise JobStoreInvariantError("publication failure diagnostics disagree")
        if not publication_failure and publication.stage != "not_ready":
            raise JobStoreInvariantError(
                "non-publication failure retains publication state"
            )


def _audit_execution_lifecycle(
    db: sqlite3.Connection, plan: ReproductionPlan, status: RunStatus
) -> None:
    terminal = sum(
        item.state in {"succeeded", "failed"} for item in status.checkpoints
    ) + len(_dependency_skip_command_pks(db, status.run_id))
    if terminal != status.completed_executions:
        raise JobStoreInvariantError("completed execution count is inconsistent")
    if len(status.checkpoints) > len(plan.executions):
        raise JobStoreInvariantError("checkpoint count exceeds accepted executions")
    active = [item for item in status.checkpoints if item.state == "active"]
    if len(active) > plan.jobs:
        raise JobStoreInvariantError("active execution count exceeds job limit")
    running_workers = int(
        db.execute(
            "SELECT COUNT(*) FROM workers WHERE run_id=? AND state='running'",
            (status.run_id,),
        ).fetchone()[0]
    )
    attached = [item for item in status.checkpoints if item.permit_id is not None]
    if status.status is not None and (active or attached or running_workers):
        raise JobStoreInvariantError("terminal run retains active work")
    orphan_running_workers = int(
        db.execute(
            "SELECT COUNT(*) FROM workers w LEFT JOIN execution_checkpoints p "
            "ON p.run_id=w.run_id AND p.command_pk=w.command_pk "
            "WHERE w.run_id=? AND w.state='running' AND w.command_pk IS NOT NULL "
            "AND (p.command_pk IS NULL OR p.state<>'active')",
            (status.run_id,),
        ).fetchone()[0]
    )
    if orphan_running_workers:
        raise JobStoreInvariantError("running worker has no active checkpoint")


def _audit_comparison_counts(db: sqlite3.Connection, status: RunStatus) -> None:
    comparison_counts = {
        cast(str, row["outcome"]): int(row["count"])
        for row in db.execute(
            "SELECT outcome, COUNT(*) AS count FROM artifact_comparisons "
            "WHERE run_id=? GROUP BY outcome",
            (status.run_id,),
        )
    }
    for outcome, count in status.artifact_outcomes.items():
        if count != comparison_counts.get(outcome, 0):
            raise JobStoreInvariantError("artifact outcome count is inconsistent")


def _audit_publication_state(publication: PublicationStateProjection) -> None:
    if (
        publication.stage == "not_ready"
        and publication.publication_identity is not None
    ):
        raise JobStoreInvariantError("unprepared publication has an identity")
    if publication.stage in {"result_committed", "complete"}:
        if publication.result_generation is None:
            raise JobStoreInvariantError("committed result has no generation")
    if publication.stage == "complete" and publication.report_generation is None:
        raise JobStoreInvariantError("complete publication has no report generation")
    has_failure = publication.failure_code is not None
    if has_failure and publication.stage not in {"ready", "result_committed"}:
        raise JobStoreInvariantError("publication failure has an invalid retry stage")
    if publication.stage == "complete" and has_failure:
        raise JobStoreInvariantError("complete publication retains a failure")


def _audit_mutable_rows(
    db: sqlite3.Connection,
    status: RunStatus,
    comparisons: Sequence[ExecutionComparisonWrite],
) -> None:
    _audit_run_ownership(db, status)
    for comparison in comparisons:
        _audit_comparison_write(db, status.run_id, comparison)
    effects = db.execute(
        "SELECT comparison_recorded_at, requirement_cleared_at "
        "FROM execution_effects WHERE run_id=?",
        (status.run_id,),
    ).fetchall()
    if len(effects) != len(comparisons):
        raise JobStoreInvariantError("execution effect inventory is inconsistent")
    for effect in effects:
        _require_timestamp(effect["comparison_recorded_at"], "comparison effect time")
        _require_optional_timestamp(
            cast(str | None, effect["requirement_cleared_at"]),
            "requirement effect time",
        )
    _audit_accepted_material_ownership(db, status.run_id)


def _audit_run_ownership(db: sqlite3.Connection, status: RunStatus) -> None:
    owner = db.execute(
        "SELECT supervisor_pid, state, registered_at, last_observed_at "
        "FROM run_owner WHERE run_id=?",
        (status.run_id,),
    ).fetchone()
    if owner is not None:
        projected_owner = RunOwner(
            int(owner["supervisor_pid"]),
            cast(Literal["running", "stopped", "exited"], owner["state"]),
            cast(str, owner["registered_at"]),
            cast(str, owner["last_observed_at"]),
        )
        _validate_run_owner(projected_owner)
        if status.status is not None and projected_owner.state == "running":
            raise JobStoreInvariantError("terminal run retains a running owner")
    running_workers = int(
        db.execute(
            "SELECT COUNT(*) FROM workers WHERE run_id=? AND state='running'",
            (status.run_id,),
        ).fetchone()[0]
    )
    if running_workers and (owner is None or owner["state"] != "running"):
        raise JobStoreInvariantError("live workers have no running owner")


def _audit_comparison_write(
    db: sqlite3.Connection,
    accepted_run_id: str,
    comparison: ExecutionComparisonWrite,
) -> None:
    _validate_comparison_write(comparison)
    run_id, command_pk = _command_identity(
        db,
        comparison.entry,
        comparison.cid,
        comparison.execution_id,
        runnable=True,
    )
    if run_id != accepted_run_id:
        raise JobStoreInvariantError("stored comparison belongs to another run")
    accepted_artifacts = {
        cast(str, row["artifact"])
        for row in db.execute(
            "SELECT artifact FROM accepted_command_outputs "
            "WHERE run_id=? AND command_pk=?",
            (run_id, command_pk),
        )
    }
    if {artifact.artifact for artifact in comparison.artifacts} != accepted_artifacts:
        raise JobStoreInvariantError("stored comparison output inventory is incomplete")
    checkpoint = db.execute(
        "SELECT state FROM execution_checkpoints WHERE run_id=? AND command_pk=?",
        (run_id, command_pk),
    ).fetchone()
    dependency_skip = _is_dependency_skip(comparison)
    if checkpoint is None and not dependency_skip:
        raise JobStoreInvariantError("stored comparison has no terminal owner")
    if checkpoint is None and not _has_failed_dependency(db, run_id, command_pk):
        raise JobStoreInvariantError("stored dependency skip has no failed dependency")
    if checkpoint is not None and (
        checkpoint["state"] not in {"succeeded", "failed"} or dependency_skip
    ):
        raise JobStoreInvariantError("stored comparison owner state is invalid")
    for artifact in comparison.artifacts:
        _verify_accepted_comparison_input(db, run_id, command_pk, artifact)


def _dependency_skip_command_pks(db: sqlite3.Connection, run_id: str) -> frozenset[int]:
    skipped: set[int] = set()
    for row in db.execute(
        "SELECT s.command_pk FROM staged_executions s "
        "LEFT JOIN execution_checkpoints p USING(run_id, command_pk) "
        "WHERE s.run_id=? AND p.command_pk IS NULL",
        (run_id,),
    ):
        command_pk = int(row["command_pk"])
        expected = int(
            db.execute(
                "SELECT COUNT(*) FROM accepted_command_outputs "
                "WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            ).fetchone()[0]
        )
        actual = int(
            db.execute(
                "SELECT COUNT(*) FROM artifact_comparisons "
                "WHERE run_id=? AND command_pk=?",
                (run_id, command_pk),
            ).fetchone()[0]
        )
        if (
            expected > 0
            and expected == actual
            and _stored_dependency_skip(db, run_id, command_pk)
        ):
            skipped.add(command_pk)
    return frozenset(skipped)


def _audit_accepted_material_ownership(db: sqlite3.Connection, run_id: str) -> None:
    orphaned = int(
        db.execute(
            "SELECT COUNT(*) FROM accepted_materials m WHERE m.run_id=? AND "
            "NOT EXISTS (SELECT 1 FROM accepted_comparison_materials x "
            "WHERE x.run_id=m.run_id AND x.material_pk=m.material_pk) AND "
            "NOT EXISTS (SELECT 1 FROM accepted_command_inputs x "
            "WHERE x.run_id=m.run_id AND x.material_pk=m.material_pk) AND "
            "NOT EXISTS (SELECT 1 FROM accepted_command_outputs x "
            "WHERE x.run_id=m.run_id AND x.material_pk=m.material_pk) AND "
            "NOT EXISTS (SELECT 1 FROM accepted_command_code x "
            "WHERE x.run_id=m.run_id AND x.material_pk=m.material_pk)",
            (run_id,),
        ).fetchone()[0]
    )
    if orphaned:
        raise JobStoreInvariantError("accepted material has no immutable owner")


def _validate_accepted_job(run_root: Path, accepted: AcceptedJob) -> None:
    try:
        plan = ReproductionPlan.from_json(accepted.plan.serialized().encode("utf-8"))
    except ValueError as error:
        raise JobStoreInvariantError(str(error)) from error
    if _RUN_ID_RE.fullmatch(accepted.run_id) is None:
        raise JobStoreInvariantError("accepted run ID is invalid")
    _require_timestamp(accepted.accepted_at, "accepted time")
    if not is_canonical_run_path(accepted.run_path):
        raise JobStoreInvariantError("accepted run path is invalid")
    for value, label in (
        (accepted.workspace_path, "workspace path"),
        (accepted.diagnostics_path, "diagnostics path"),
    ):
        _require_relative_path(value, label)
    for command in plan.commands:
        _validate_command_snapshot(command)
    project_roots = {cast(str, item["project_root"]) for item in plan.commands}
    if len(project_roots) != 1:
        raise JobStoreInvariantError("accepted commands do not share one project root")
    _validate_run_location(
        run_root,
        Path(next(iter(project_roots))),
        accepted.run_path,
        accepted.accepted_at,
        accepted.run_id,
    )


def _validate_opened_run_root(db: sqlite3.Connection, run_root: Path) -> None:
    run = _sole_row(db, "SELECT run_id, run_path, accepted_at FROM runs")
    project_roots = db.execute(
        "SELECT DISTINCT project_root FROM accepted_commands LIMIT 2"
    ).fetchall()
    if len(project_roots) != 1:
        raise JobStoreInvariantError("accepted commands do not share one project root")
    _validate_run_location(
        run_root,
        Path(cast(str, project_roots[0]["project_root"])),
        cast(str, run["run_path"]),
        cast(str, run["accepted_at"]),
        cast(str, run["run_id"]),
    )


def _validate_run_location(
    run_root: Path,
    project_root: Path,
    run_path: str,
    accepted_at: str,
    run_id: str,
) -> None:
    try:
        actual = project_tmp_relative(run_root, project_root)
    except OSError as error:
        raise JobStoreInvariantError(str(error)) from error
    logical = PurePosixPath(run_path)
    if (
        not is_canonical_run_path(run_path)
        or actual != run_path
        or logical.parts[2] != accepted_at[:10]
        or not logical.name.endswith(f"-{run_id}")
    ):
        raise JobStoreInvariantError(
            "accepted run location does not match its identity"
        )


def _validate_command_snapshot(command: Mapping[str, object]) -> None:
    selection = command.get("selection")
    prior = command.get("prior_disposition")
    digest = command.get("source_digest")
    if selection not in {"blocked", "not_needed", "policy", "run", "unchanged"}:
        raise JobStoreInvariantError("accepted command selection is invalid")
    if (selection == "unchanged") != (prior in {"failed", "blocked"}) or (
        selection != "unchanged" and prior is not None
    ):
        raise JobStoreInvariantError("accepted command prior disposition is invalid")
    if digest is not None and (
        not isinstance(digest, str) or _SOURCE_DIGEST_RE.fullmatch(digest) is None
    ):
        raise JobStoreInvariantError("accepted command source digest is invalid")
    if selection not in {"not_needed", "policy"} and digest is None:
        raise JobStoreInvariantError("accepted command source digest is missing")


def _validate_permit_attachment(attachment: ExecutionPermitAttachment) -> None:
    _require_execution_identity(
        attachment.entry, attachment.cid, attachment.execution_id
    )
    if not _bounded_string(attachment.permit_id):
        raise JobStoreInvariantError("execution permit is invalid")
    _require_timestamp(attachment.checkpointed_at, "permit attachment time")


def _validate_execution_start(start: ExecutionStart) -> None:
    _require_execution_identity(start.entry, start.cid, start.execution_id)
    if not _bounded_string(start.permit_id):
        raise JobStoreInvariantError("execution start permit is empty")
    _require_timestamp(start.checkpointed_at, "checkpoint time")
    _require_timestamp(start.started_at, "start time")
    if (
        isinstance(start.elapsed_seconds, bool)
        or not isinstance(start.elapsed_seconds, (int, float))
        or not math.isfinite(start.elapsed_seconds)
        or start.elapsed_seconds < 0
    ):
        raise JobStoreInvariantError("execution elapsed time is invalid")
    for value, label in (
        (start.stdout_path, "stdout path"),
        (start.stderr_path, "stderr path"),
    ):
        if value is not None:
            _require_relative_path(value, label)
    _require_scratch_path(start.scratch_path, "scratch path")


def _validate_execution_terminal(terminal: ExecutionTerminal) -> None:
    _require_execution_identity(terminal.entry, terminal.cid, terminal.execution_id)
    if not _bounded_string(terminal.permit_id):
        raise JobStoreInvariantError("terminal checkpoint permit is invalid")
    if terminal.state not in _TERMINAL_CHECKPOINT_STATES:
        raise JobStoreInvariantError("terminal checkpoint state is invalid")
    _require_timestamp(terminal.checkpointed_at, "checkpoint time")
    _validate_terminal_timing(terminal)
    _validate_terminal_failure(terminal)
    _validate_terminal_outputs(terminal.outputs)


def _validate_terminal_timing(terminal: ExecutionTerminal) -> None:
    if terminal.state == "stopped":
        if terminal.finished_at is not None:
            raise JobStoreInvariantError("stopped checkpoint has a finish time")
    elif terminal.finished_at is None:
        raise JobStoreInvariantError("terminal checkpoint has no finish time")
    else:
        _require_timestamp(terminal.finished_at, "finish time")
    if (
        isinstance(terminal.elapsed_seconds, bool)
        or not isinstance(terminal.elapsed_seconds, (int, float))
        or not math.isfinite(terminal.elapsed_seconds)
        or terminal.elapsed_seconds < 0
    ):
        raise JobStoreInvariantError("execution elapsed time is invalid")


def _validate_terminal_failure(terminal: ExecutionTerminal) -> None:
    failure = (
        terminal.failure_code,
        terminal.failure_message,
        terminal.failure_recorded_at,
    )
    if terminal.state == "succeeded":
        if any(value is not None for value in failure):
            raise JobStoreInvariantError("successful checkpoint has a failure")
    elif not all(_bounded_string(value) for value in failure):
        raise JobStoreInvariantError("failed or stopped checkpoint needs a failure")
    if terminal.failure_recorded_at is not None:
        _require_timestamp(terminal.failure_recorded_at, "failure time")


def _validate_terminal_outputs(outputs: Sequence[CheckpointOutput]) -> None:
    artifacts = [item.artifact for item in outputs]
    if len(artifacts) > MAX_CHECKPOINT_OUTPUTS:
        raise JobStoreInvariantError("checkpoint output count crossed its bound")
    if len(artifacts) != len(set(artifacts)):
        raise JobStoreInvariantError("terminal checkpoint output is duplicated")
    for output in outputs:
        if not _bounded_string(output.artifact) or not isinstance(
            output.fingerprint, Mapping
        ):
            raise JobStoreInvariantError("terminal checkpoint output is invalid")


def _validate_terminal_output_ownership(
    db: sqlite3.Connection,
    run_id: str,
    command_pk: int,
    terminal: ExecutionTerminal,
) -> None:
    accepted_outputs = {
        cast(str, item["artifact"]): cast(str, item["kind"])
        for item in db.execute(
            "SELECT artifact, kind FROM accepted_command_outputs "
            "WHERE run_id=? AND command_pk=?",
            (run_id, command_pk),
        )
    }
    terminal_outputs = {item.artifact for item in terminal.outputs}
    if not terminal_outputs <= accepted_outputs.keys():
        raise JobStoreInvariantError("terminal checkpoint has an unaccepted output")
    if terminal.state == "succeeded" and terminal_outputs != accepted_outputs.keys():
        raise JobStoreInvariantError(
            "successful checkpoint output inventory is incomplete"
        )
    for output in terminal.outputs:
        _validate_fingerprint(
            output.fingerprint,
            accepted_outputs[output.artifact],
            f"terminal output {output.artifact}",
        )


def _require_execution_identity(entry: str, cid: str, execution_id: str) -> None:
    if (
        ENTRY_ID_RE.fullmatch(entry) is None
        or _CID_RE.fullmatch(cid) is None
        or _EXECUTION_ID_RE.fullmatch(execution_id) is None
    ):
        raise JobStoreInvariantError("execution identity is invalid")


def _validate_run_failure(failure: RunFailure) -> None:
    if not _bounded_string(failure.code) or not _bounded_string(failure.message):
        raise JobStoreInvariantError("run failure is incomplete")
    _require_timestamp(failure.recorded_at, "run failure time")


def _validate_identity_value(
    identity: ExecutionIdentity, value: str, label: str
) -> None:
    _require_execution_identity(identity.entry, identity.cid, identity.execution_id)
    if not _bounded_string(value):
        raise JobStoreInvariantError(f"execution {label} is invalid")


def _require_resumable_checkpoint(row: sqlite3.Row | None) -> None:
    if (
        row is None
        or row["state"] != "stopped"
        or row["permit_id"] is not None
        or row["scratch_path"] is not None
    ):
        raise JobStoreTransitionError("permit expected one cleaned stopped checkpoint")


def _require_no_running_worker(
    db: sqlite3.Connection, run_id: str, command_pk: int
) -> None:
    running = int(
        db.execute(
            "SELECT COUNT(*) FROM workers WHERE run_id=? AND command_pk=? "
            "AND state='running'",
            (run_id, command_pk),
        ).fetchone()[0]
    )
    if running:
        raise JobStoreTransitionError("execution retains a running worker")


def _require_quiescent_run(db: sqlite3.Connection, run_id: str) -> None:
    active = int(
        db.execute(
            "SELECT COUNT(*) FROM execution_checkpoints WHERE run_id=? "
            "AND (state='active' OR permit_id IS NOT NULL)",
            (run_id,),
        ).fetchone()[0]
    )
    running = int(
        db.execute(
            "SELECT COUNT(*) FROM workers WHERE run_id=? AND state='running'",
            (run_id,),
        ).fetchone()[0]
    )
    if active or running:
        raise JobStoreTransitionError("run retains active ownership")


def _command_identity(
    db: sqlite3.Connection,
    entry: str,
    cid: str,
    execution_id: str,
    *,
    runnable: bool,
) -> tuple[str, int]:
    join = " JOIN accepted_executions e USING(run_id, command_pk)" if runnable else ""
    rows = db.execute(
        "SELECT c.run_id, c.command_pk FROM accepted_commands c"
        + join
        + " WHERE c.entry=? AND c.cid=? AND c.execution_id=?",
        (entry, cid, execution_id),
    ).fetchall()
    if len(rows) != 1:
        raise JobStoreInvariantError("execution is not uniquely accepted and runnable")
    return cast(str, rows[0]["run_id"]), int(rows[0]["command_pk"])


def _declaration_index(value: object) -> Mapping[str, Mapping[str, object]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or not isinstance(value.get("inputs"), list):
        raise JobStoreInvariantError("accepted data declaration is invalid")
    result: dict[str, Mapping[str, object]] = {}
    for item in cast(Sequence[object], value["inputs"]):
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            raise JobStoreInvariantError("accepted data declaration is invalid")
        result[cast(str, item["name"])] = cast(Mapping[str, object], item)
    return result


def _regular_run_root(run_root: Path) -> Path:
    try:
        if run_root.is_symlink() or not run_root.is_dir():
            raise JobStoreSymlinkError(
                f"run root is not a regular directory: {run_root}"
            )
        return run_root.resolve(strict=True)
    except OSError as error:
        if isinstance(error, JobStoreError):
            raise
        raise JobStoreMalformedError(str(error)) from error


def _checked_state_path(run_root: Path, *, writable: bool) -> Path:
    try:
        path = job_state_path(run_root)
    except OSError as error:
        raise JobStoreMalformedError(str(error)) from error
    for candidate in (
        path,
        *(Path(str(path) + suffix) for suffix in _COMPANIONS),
        run_root / JOB_LOCK_NAME,
    ):
        if candidate.is_symlink():
            raise JobStoreSymlinkError(f"unsafe job-state path: {candidate}")
    if not writable and not path.is_file():
        raise JobStoreMissingError(f"job state is absent: {path}")
    return path


@contextmanager
def _job_mutex(run_root: Path) -> Iterator[None]:
    lock_path = run_root / JOB_LOCK_NAME
    if lock_path.is_symlink():
        raise JobStoreSymlinkError(f"unsafe job-state lock: {lock_path}")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o644)
    except OSError as error:
        raise JobStoreMalformedError(str(error)) from error
    with os.fdopen(descriptor, "r+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise JobStoreBusyError(f"job state is active: {lock_path}") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _open_database(
    path: Path, *, mode: Literal["rw", "rwc"], allow_uninitialized: bool = False
) -> sqlite3.Connection:
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(
            path.as_uri() + f"?mode={mode}", uri=True, timeout=0, isolation_level=None
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version == 0 and allow_uninitialized:
            return db
        if version != JOB_STORE_VERSION:
            raise JobStoreUnsupportedError(
                f"job store version {version} is unsupported"
            )
        _check_store_size(path)
        return db
    except BaseException as error:
        if db is not None:
            db.close()
        _raise_storage_error(error)


def _initialize_schema(db: sqlite3.Connection) -> None:
    statement = ""
    for line in _DDL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""
    if statement.strip():
        raise JobStoreMalformedError("incomplete durable job schema")
    db.execute(f"PRAGMA user_version={JOB_STORE_VERSION}")


def _check_store_size(path: Path) -> None:
    if _store_size(path) > MAX_JOB_STORE_BYTES:
        raise JobStoreInvariantError("durable job store crossed its byte bound")


def _check_database_size(db: sqlite3.Connection) -> None:
    """Reject a transaction before commit when its allocated data is oversized."""

    page_count = int(db.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
    if page_count * page_size > MAX_JOB_STORE_BYTES:
        raise JobStoreInvariantError("durable job store crossed its byte bound")


def _store_size(path: Path) -> int:
    """Return bounded SQLite data bytes including safe transaction companions."""

    try:
        return sum(
            candidate.stat().st_size
            for candidate in (
                path,
                *(Path(str(path) + suffix) for suffix in _COMPANIONS),
            )
            if candidate.exists()
        )
    except OSError as error:
        raise JobStoreMalformedError(str(error)) from error


def _remove_failed_creation(path: Path) -> None:
    for candidate in (path, *(Path(str(path) + suffix) for suffix in _COMPANIONS)):
        try:
            if (
                candidate.exists()
                and candidate.is_file()
                and not candidate.is_symlink()
            ):
                candidate.unlink()
        except OSError:
            pass


def _sole_row(
    db: sqlite3.Connection, query: str, parameters: Sequence[object] = ()
) -> sqlite3.Row:
    rows = db.execute(query, tuple(parameters)).fetchall()
    if len(rows) != 1:
        raise JobStoreInvariantError("durable job store has no unique run row")
    return rows[0]


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise JobStoreInvariantError("accepted leaf JSON is invalid") from error


def _optional_json(value: object | None) -> str | None:
    return _canonical_json(value) if value is not None else None


def _decode_json(value: object, expected: type[dict] | type[list]) -> object:
    if not isinstance(value, str):
        raise JobStoreInvariantError("stored leaf JSON is not text")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise JobStoreInvariantError("stored leaf JSON is malformed") from error
    if not isinstance(decoded, expected) or _canonical_json(decoded) != value:
        raise JobStoreInvariantError("stored leaf JSON is noncanonical")
    return decoded


def _decode_fingerprint(value: object, kind: str, subject: str) -> Mapping[str, object]:
    decoded = cast(Mapping[str, object], _decode_json(value, dict))
    _validate_fingerprint(decoded, kind, subject)
    return decoded


def _validate_fingerprint(value: object, kind: str, subject: str) -> None:
    try:
        parse_fingerprint(value, subject, kind=kind)
    except DataContractError as error:
        raise JobStoreInvariantError(f"{subject} is invalid") from error


def _require_timestamp(value: str, label: str) -> None:
    try:
        valid = (
            isinstance(value, str)
            and _TIMESTAMP_RE.fullmatch(value) is not None
            and datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
            == value
        )
    except ValueError:
        valid = False
    if not valid:
        raise JobStoreInvariantError(f"{label} is invalid")


def _require_optional_timestamp(value: str | None, label: str) -> None:
    if value is not None:
        _require_timestamp(value, label)


def _require_relative_path(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_PATH_BYTES
    ):
        raise JobStoreInvariantError(f"{label} is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise JobStoreInvariantError(f"{label} is invalid")


def _validate_run_status_projection(status: RunStatus) -> None:
    _validate_status_identity(status)
    _validate_status_lifecycle(status)
    _validate_status_progress(status)
    _validate_status_children(status)


def _validate_status_identity(status: RunStatus) -> None:
    if _RUN_ID_RE.fullmatch(status.run_id) is None:
        raise JobStoreInvariantError("stored run ID is invalid")
    if not _bounded_string(status.summary):
        raise JobStoreInvariantError("stored run summary is invalid")
    if set(status.target) != {"kind", "entry"} or status.target.get("kind") not in {
        "log",
        "entry",
    }:
        raise JobStoreInvariantError("stored run target is invalid")
    target_entry = status.target.get("entry")
    if (status.target["kind"] == "log") != (target_entry is None):
        raise JobStoreInvariantError("stored run target is inconsistent")
    if target_entry is not None and (
        not isinstance(target_entry, str) or ENTRY_ID_RE.fullmatch(target_entry) is None
    ):
        raise JobStoreInvariantError("stored run target entry is invalid")
    if status.jobs < 1 or status.execution_timeout_seconds < 1:
        raise JobStoreInvariantError("stored run settings are invalid")
    _require_timestamp(status.accepted_at, "accepted time")
    if not is_canonical_run_path(status.run_path):
        raise JobStoreInvariantError("stored run path is invalid")


def _validate_status_lifecycle(status: RunStatus) -> None:
    phases = {
        None,
        "accepted",
        "planning",
        "preflight",
        "executing",
        "comparing",
        "publishing",
        "stopping",
    }
    if status.status not in {None, "complete", "stopped", "failed"}:
        raise JobStoreInvariantError("stored run lifecycle value is invalid")
    if status.phase not in phases:
        raise JobStoreInvariantError("stored run lifecycle value is invalid")
    if (status.status is None) == (status.phase is None):
        raise JobStoreInvariantError("stored run lifecycle is invalid")
    for value, label in (
        (status.stop_requested_at, "stop request time"),
        (status.started_at, "run start time"),
        (status.resumed_at, "run resume time"),
        (status.stopped_at, "run stop time"),
        (status.finished_at, "run finish time"),
    ):
        _require_optional_timestamp(value, label)
    _require_timestamp(status.updated_at, "run update time")
    if status.status == "stopped" and (
        status.stopped_at is None or status.finished_at is not None
    ):
        raise JobStoreInvariantError("stored stopped lifecycle is inconsistent")
    if status.status in {"complete", "failed"} and status.finished_at is None:
        raise JobStoreInvariantError("stored terminal lifecycle has no finish time")
    if status.status is None and (
        status.stopped_at is not None or status.finished_at is not None
    ):
        raise JobStoreInvariantError("stored active lifecycle has terminal time")
    _validate_status_intent(status)


def _validate_status_intent(status: RunStatus) -> None:
    if status.stop_requested_at is not None and (
        status.status is None and status.phase != "stopping"
    ):
        raise JobStoreInvariantError("stored stop request has an invalid phase")
    if status.operational_failure is not None and (
        status.status is None and status.phase != "stopping"
    ):
        raise JobStoreInvariantError("stored operational failure has an invalid phase")
    if (
        status.status in {"stopped", "complete"}
        and status.operational_failure is not None
    ):
        raise JobStoreInvariantError("non-failed run retains operational failure")
    if status.status == "failed" and status.operational_failure is None:
        raise JobStoreInvariantError("failed run has no operational failure")


def _validate_status_progress(status: RunStatus) -> None:
    if status.completed_executions < 0 or status.total_executions < 0:
        raise JobStoreInvariantError("stored run progress is invalid")
    if set(status.artifact_outcomes) != _empty_outcome_counts().keys() or any(
        not isinstance(value, int) or value < 0
        for value in status.artifact_outcomes.values()
    ):
        raise JobStoreInvariantError("stored artifact outcomes are invalid")


def _validate_status_children(status: RunStatus) -> None:
    _validate_diagnostic_projection(status.latest_execution_diagnostic, execution=True)
    _validate_diagnostic_projection(status.operational_failure, execution=False)
    if len(status.checkpoints) > 2_048:
        raise JobStoreInvariantError("checkpoint count crossed its bound")
    for checkpoint in status.checkpoints:
        _validate_checkpoint_projection(checkpoint)
        if checkpoint.state == "active" and checkpoint.permit_id is None:
            raise JobStoreInvariantError("active checkpoint has no current permit")
        if status.status is not None and checkpoint.permit_id is not None:
            raise JobStoreInvariantError("terminal run retains an attached permit")
    if len(status.workers) > MAX_RUN_WORKERS:
        raise JobStoreInvariantError("run worker count crossed its bound")
    worker_groups: dict[tuple[str, str, str] | None, list[WorkerRecord]] = {}
    for identity, worker in status.workers:
        key: tuple[str, str, str] | None = None
        if identity is not None:
            _require_execution_identity(
                identity.entry, identity.cid, identity.execution_id
            )
            key = (identity.entry, identity.cid, identity.execution_id)
        worker_groups.setdefault(key, []).append(worker)
    for workers in worker_groups.values():
        _validate_workers(workers)


def _validate_diagnostic_projection(
    diagnostic: DiagnosticProjection | None, *, execution: bool | None
) -> None:
    if diagnostic is None:
        return
    if not _bounded_string(diagnostic.code) or not _bounded_string(diagnostic.message):
        raise JobStoreInvariantError("stored diagnostic is incomplete")
    _require_timestamp(diagnostic.recorded_at, "diagnostic time")
    if execution:
        if (
            diagnostic.entry is None
            or diagnostic.cid is None
            or diagnostic.execution_id is None
        ):
            raise JobStoreInvariantError("stored execution diagnostic has no identity")
        _require_execution_identity(
            diagnostic.entry, diagnostic.cid, diagnostic.execution_id
        )
    elif execution is False and (
        diagnostic.entry is not None
        or diagnostic.cid is not None
        or diagnostic.execution_id is not None
    ):
        raise JobStoreInvariantError("stored run diagnostic has an execution identity")
    elif (
        len(
            {
                diagnostic.entry is None,
                diagnostic.cid is None,
                diagnostic.execution_id is None,
            }
        )
        != 1
    ):
        raise JobStoreInvariantError("stored diagnostic has a partial identity")
    elif (
        diagnostic.entry is not None
        and diagnostic.cid is not None
        and diagnostic.execution_id is not None
    ):
        _require_execution_identity(
            diagnostic.entry, diagnostic.cid, diagnostic.execution_id
        )


def _validate_checkpoint_projection(checkpoint: CheckpointProjection) -> None:
    _require_execution_identity(
        checkpoint.entry, checkpoint.cid, checkpoint.execution_id
    )
    if checkpoint.state not in _CHECKPOINT_STATES:
        raise JobStoreInvariantError("stored checkpoint state is invalid")
    if checkpoint.permit_id is not None and not _bounded_string(checkpoint.permit_id):
        raise JobStoreInvariantError("stored checkpoint permit is invalid")
    if checkpoint.released_permit_id is not None and not _bounded_string(
        checkpoint.released_permit_id
    ):
        raise JobStoreInvariantError("stored released checkpoint permit is invalid")
    _validate_checkpoint_timing(checkpoint)
    _validate_checkpoint_failure(checkpoint)
    _validate_checkpoint_paths(checkpoint)
    _validate_checkpoint_outputs(checkpoint.outputs)


def _validate_checkpoint_timing(checkpoint: CheckpointProjection) -> None:
    _require_timestamp(checkpoint.checkpointed_at, "checkpoint time")
    _require_optional_timestamp(checkpoint.started_at, "checkpoint start time")
    _require_optional_timestamp(checkpoint.finished_at, "checkpoint finish time")
    if not math.isfinite(checkpoint.elapsed_seconds) or checkpoint.elapsed_seconds < 0:
        raise JobStoreInvariantError("stored checkpoint elapsed time is invalid")


def _validate_checkpoint_failure(checkpoint: CheckpointProjection) -> None:
    failure = (
        checkpoint.failure_code,
        checkpoint.failure_message,
        checkpoint.failure_recorded_at,
    )
    if checkpoint.state in {"active", "succeeded"}:
        if any(value is not None for value in failure):
            raise JobStoreInvariantError("stored checkpoint failure is inconsistent")
    elif not all(_bounded_string(value) for value in failure):
        raise JobStoreInvariantError("stored checkpoint failure is incomplete")
    if checkpoint.state == "active" and checkpoint.finished_at is not None:
        raise JobStoreInvariantError("active checkpoint has a finish time")
    if checkpoint.state == "stopped" and checkpoint.finished_at is not None:
        raise JobStoreInvariantError("stopped checkpoint has a finish time")
    if checkpoint.state in {"succeeded", "failed"} and (
        checkpoint.started_at is None or checkpoint.finished_at is None
    ):
        raise JobStoreInvariantError("terminal checkpoint has incomplete timing")
    if checkpoint.failure_recorded_at is not None:
        _require_timestamp(checkpoint.failure_recorded_at, "checkpoint failure time")


def _validate_checkpoint_paths(checkpoint: CheckpointProjection) -> None:
    for value, label in (
        (checkpoint.stdout_path, "checkpoint stdout path"),
        (checkpoint.stderr_path, "checkpoint stderr path"),
    ):
        if value is not None:
            _require_relative_path(value, label)
    if checkpoint.scratch_path is not None:
        _require_scratch_path(checkpoint.scratch_path, "stored checkpoint scratch path")


def _validate_checkpoint_outputs(outputs: Sequence[CheckpointOutput]) -> None:
    if len(outputs) > MAX_CHECKPOINT_OUTPUTS:
        raise JobStoreInvariantError("checkpoint output count crossed its bound")
    if len({output.artifact for output in outputs}) != len(outputs):
        raise JobStoreInvariantError("stored checkpoint output is duplicated")
    for output in outputs:
        if not _bounded_string(output.artifact) or not isinstance(
            output.fingerprint, Mapping
        ):
            raise JobStoreInvariantError("stored checkpoint output is invalid")


def _require_bounded_status(status: RunStatus) -> None:
    encoded = _canonical_json(asdict(status)).encode("utf-8")
    if len(encoded) > MAX_STATUS_BYTES:
        raise JobStoreInvariantError("run status crossed its byte bound")


def _bounded_string(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= MAX_STRING_BYTES
    )


def _require_scratch_path(value: str, label: str) -> None:
    if len(value.encode("utf-8")) > MAX_PATH_BYTES:
        raise JobStoreInvariantError(f"{label} is invalid")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or path.as_posix() != value
        or path.parts[:3] != ("/", "private", "tmp")
        or len(path.parts) <= 3
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise JobStoreInvariantError(f"{label} is invalid")


def _before_commit(_operation: str, _db: sqlite3.Connection) -> None:
    """Test seam for interrupting one transaction immediately before commit."""


def _authorizer(
    update_hook: Callable[[str, str], None],
) -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    """Adapt SQLite's authorizer into a table-scoped write-observation hook."""

    actions = {
        sqlite3.SQLITE_DELETE: "delete",
        sqlite3.SQLITE_INSERT: "insert",
        sqlite3.SQLITE_UPDATE: "update",
    }

    def observe(
        action: int,
        table: str | None,
        _column: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        operation = actions.get(action)
        if operation is not None and table is not None:
            update_hook(operation, table)
        return sqlite3.SQLITE_OK

    return observe


def _sqlite_error_code(error: sqlite3.OperationalError) -> type[JobStoreError]:
    text = str(error).lower()
    if "locked" in text or "busy" in text:
        return JobStoreBusyError
    return JobStoreMalformedError


def _raise_storage_error(error: BaseException) -> NoReturn:
    if isinstance(error, JobStoreError):
        raise error
    if isinstance(error, sqlite3.OperationalError):
        raise _sqlite_error_code(error)(str(error)) from error
    if isinstance(error, sqlite3.Error):
        raise JobStoreMalformedError(str(error)) from error
    if isinstance(error, (KeyError, OverflowError, TypeError, ValueError)):
        raise JobStoreMalformedError(str(error)) from error
    raise error
