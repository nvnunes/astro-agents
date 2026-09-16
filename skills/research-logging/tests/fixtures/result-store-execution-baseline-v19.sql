-- Exact shared/validation DDL from Astro-agents 2fcea591a3c56b0d29f7d5ccaf6ce2210351fce7.
-- Reproduction replacement fixture only; no production compatibility reader.

CREATE TABLE store_state (
    domain TEXT PRIMARY KEY CHECK(domain IN
        ('validation', 'reproduction', 'command')),
    generation INTEGER NOT NULL CHECK(generation >= 1),
    summary TEXT NOT NULL
);
CREATE TABLE report_materializations (
    kind TEXT PRIMARY KEY CHECK(kind IN ('validation', 'reproduction')),
    source_generation INTEGER NOT NULL CHECK(source_generation >= 1),
    content_sha256 TEXT NOT NULL,
    rendered_at TEXT NOT NULL
);
CREATE TABLE reproduction_metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    summary TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE reproduction_runs (
    run_pk INTEGER PRIMARY KEY CHECK(run_pk >= 1),
    run_id TEXT UNIQUE NOT NULL,
    target_kind TEXT NOT NULL,
    target_entry TEXT,
    target_cid TEXT,
    target_execution_id TEXT,
    include_all INTEGER NOT NULL CHECK(include_all IN (0, 1)),
    status TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    finished_at TEXT,
    folder_path TEXT NOT NULL,
    artifact_matched INTEGER NOT NULL,
    artifact_changed INTEGER NOT NULL,
    artifact_failed INTEGER NOT NULL,
    artifact_comparison_failed INTEGER NOT NULL,
    artifact_skipped INTEGER NOT NULL,
    command_not_automatic INTEGER,
    command_reproduction_not_needed INTEGER,
    command_unchanged_failed INTEGER,
    command_unchanged_blocked INTEGER,
    command_succeeded INTEGER,
    command_failed INTEGER,
    command_blocked INTEGER,
    command_total INTEGER
);
CREATE TABLE reproduction_run_commands (
    run_pk INTEGER NOT NULL,
    entry TEXT NOT NULL,
    cid TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    plan_order INTEGER NOT NULL CHECK(plan_order >= 0),
    bucket TEXT NOT NULL,
    reason TEXT NOT NULL,
    terminal_disposition TEXT,
    source_digest TEXT,
    auto_reproduce INTEGER CHECK(auto_reproduce IN (0, 1)),
    cwd TEXT,
    exclusive INTEGER CHECK(exclusive IN (0, 1)),
    prior_disposition TEXT,
    queued INTEGER CHECK(queued IN (0, 1)),
    requires_reproduction INTEGER CHECK(requires_reproduction IN (0, 1)),
    run_selection TEXT,
    details_json TEXT NOT NULL,
    recipe_json TEXT,
    PRIMARY KEY(run_pk, entry, cid, execution_id),
    FOREIGN KEY(run_pk) REFERENCES reproduction_runs(run_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE reproduction_run_executions (
    run_pk INTEGER NOT NULL,
    entry TEXT NOT NULL,
    cid TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    started_at TEXT,
    finished_at TEXT,
    elapsed_seconds REAL,
    PRIMARY KEY(run_pk, entry, cid, execution_id),
    FOREIGN KEY(run_pk) REFERENCES reproduction_runs(run_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE reproduction_execution_results (
    entry TEXT NOT NULL,
    cid TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    disposition TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    producing_run_id TEXT NOT NULL,
    PRIMARY KEY(entry, cid, execution_id)
) WITHOUT ROWID;
CREATE TABLE reproduction_artifact_results (
    entry TEXT NOT NULL,
    artifact TEXT NOT NULL,
    cid TEXT,
    execution_id TEXT,
    outcome TEXT NOT NULL,
    reason TEXT,
    recorded_at TEXT NOT NULL,
    producing_run_id TEXT NOT NULL,
    comparison_contract TEXT,
    comparison_profile TEXT,
    expected_json TEXT,
    regenerated_json TEXT,
    evidence_contract TEXT,
    evidence_definition TEXT,
    PRIMARY KEY(entry, artifact)
) WITHOUT ROWID;
CREATE TABLE reproduction_comparison_evidence (
    entry TEXT NOT NULL,
    artifact TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    record_id TEXT,
    retained_json TEXT NOT NULL,
    regenerated_json TEXT NOT NULL,
    tolerance_json TEXT NOT NULL,
    matched INTEGER NOT NULL CHECK(matched IN (0, 1)),
    PRIMARY KEY(entry, artifact, position),
    FOREIGN KEY(entry, artifact)
        REFERENCES reproduction_artifact_results(entry, artifact) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE INDEX reproduction_runs_order
    ON reproduction_runs(finished_at DESC, accepted_at DESC, run_id DESC);
CREATE INDEX reproduction_run_commands_order
    ON reproduction_run_commands(run_pk, plan_order);
CREATE INDEX reproduction_run_executions_order
    ON reproduction_run_executions(run_pk, position);
CREATE INDEX reproduction_artifact_outcome
    ON reproduction_artifact_results(outcome, entry, artifact);

CREATE TABLE validation_snapshots (
    snapshot_pk INTEGER PRIMARY KEY CHECK(snapshot_pk >= 1),
    snapshot_id TEXT UNIQUE NOT NULL,
    generation INTEGER UNIQUE NOT NULL CHECK(generation >= 1),
    slot TEXT UNIQUE NOT NULL,
    target_kind TEXT NOT NULL CHECK(target_kind IN ('log', 'entry')),
    target_log TEXT NOT NULL,
    target_entry TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('clear', 'findings', 'failed')),
    passed_check_count INTEGER NOT NULL CHECK(passed_check_count >= 0),
    finding_count INTEGER NOT NULL CHECK(finding_count >= 0),
    batch_count INTEGER NOT NULL CHECK(batch_count >= 0),
    blocked_count INTEGER NOT NULL CHECK(blocked_count >= 0),
    failed_count INTEGER NOT NULL CHECK(failed_count >= 0),
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    report_context_json TEXT NOT NULL,
    CHECK((target_kind = 'log' AND target_entry IS NULL) OR
          (target_kind = 'entry' AND target_entry IS NOT NULL)),
    CHECK((outcome = 'clear' AND failed_count = 0 AND finding_count = 0
                              AND batch_count = 0) OR
          (outcome = 'findings' AND failed_count = 0 AND finding_count >= 1
                                 AND batch_count >= 1) OR
          (outcome = 'failed' AND failed_count >= 1))
);
CREATE TABLE validation_snapshot_entries (
    snapshot_pk INTEGER NOT NULL,
    relation TEXT NOT NULL CHECK(relation IN
        ('requested', 'evaluated', 'dependency', 'repair', 'context')),
    position INTEGER NOT NULL CHECK(position >= 0),
    entry TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, relation, position),
    UNIQUE(snapshot_pk, relation, entry),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_findings (
    snapshot_pk INTEGER NOT NULL,
    finding_pk INTEGER NOT NULL CHECK(finding_pk >= 1),
    finding_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    type TEXT NOT NULL CHECK(type IN
        ('conformance', 'evidence', 'provenance', 'orphan')),
    code TEXT NOT NULL,
    entry TEXT,
    subject TEXT NOT NULL,
    rule TEXT NOT NULL,
    observed_json TEXT NOT NULL,
    admission_owner TEXT CHECK(admission_owner IS NULL OR admission_owner IN
        ('execution', 'command', 'material', 'entry', 'log')),
    PRIMARY KEY(snapshot_pk, finding_pk),
    UNIQUE(snapshot_pk, finding_id),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_blocked_checks (
    snapshot_pk INTEGER NOT NULL,
    check_pk INTEGER NOT NULL CHECK(check_pk >= 1),
    check_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    area TEXT NOT NULL CHECK(area IN
        ('conformance', 'evidence', 'provenance', 'orphan')),
    entry TEXT,
    subject TEXT NOT NULL,
    rule TEXT NOT NULL,
    blocked_by_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, check_pk),
    UNIQUE(snapshot_pk, check_id),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_failed_checks (
    snapshot_pk INTEGER NOT NULL,
    check_pk INTEGER NOT NULL CHECK(check_pk >= 1),
    check_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    area TEXT NOT NULL CHECK(area IN
        ('conformance', 'evidence', 'provenance', 'orphan')),
    entry TEXT,
    subject TEXT NOT NULL,
    code TEXT NOT NULL,
    rule TEXT NOT NULL,
    operation TEXT NOT NULL CHECK(operation IN
        ('read', 'observe', 'cache', 'graph', 'other')),
    reason_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, check_pk),
    UNIQUE(snapshot_pk, check_id),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_finding_causes (
    snapshot_pk INTEGER NOT NULL,
    finding_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    cause_finding_pk INTEGER NOT NULL,
    PRIMARY KEY(snapshot_pk, finding_pk, position),
    UNIQUE(snapshot_pk, finding_pk, cause_finding_pk),
    FOREIGN KEY(snapshot_pk, finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, cause_finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_finding_source_locations (
    snapshot_pk INTEGER NOT NULL,
    finding_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    path TEXT NOT NULL,
    line INTEGER CHECK(line IS NULL OR line >= 1),
    PRIMARY KEY(snapshot_pk, finding_pk, position),
    FOREIGN KEY(snapshot_pk, finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_repair_keys (
    snapshot_pk INTEGER NOT NULL,
    repair_key_pk INTEGER NOT NULL CHECK(repair_key_pk >= 1),
    kind TEXT NOT NULL,
    identity TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, repair_key_pk),
    UNIQUE(snapshot_pk, kind, identity),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_finding_repair_keys (
    snapshot_pk INTEGER NOT NULL,
    finding_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    repair_key_pk INTEGER NOT NULL,
    repair_entry TEXT,
    PRIMARY KEY(snapshot_pk, finding_pk, position),
    UNIQUE(snapshot_pk, finding_pk, repair_key_pk),
    FOREIGN KEY(snapshot_pk, finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, repair_key_pk)
        REFERENCES validation_repair_keys(snapshot_pk, repair_key_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_repair_nodes (
    snapshot_pk INTEGER NOT NULL,
    node_pk INTEGER NOT NULL CHECK(node_pk >= 1),
    node_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    kind TEXT NOT NULL,
    identity TEXT NOT NULL,
    entry TEXT,
    attributes_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, node_pk),
    UNIQUE(snapshot_pk, node_id),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_repair_edges (
    snapshot_pk INTEGER NOT NULL,
    edge_pk INTEGER NOT NULL CHECK(edge_pk >= 1),
    position INTEGER NOT NULL CHECK(position >= 0),
    relation_kind TEXT NOT NULL CHECK(relation_kind IN
        ('established', 'ambiguous')),
    kind TEXT NOT NULL,
    subject_node_pk INTEGER NOT NULL,
    observed_json TEXT,
    PRIMARY KEY(snapshot_pk, edge_pk),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk, subject_node_pk)
        REFERENCES validation_repair_nodes(snapshot_pk, node_pk) ON DELETE CASCADE,
    CHECK((relation_kind = 'established' AND observed_json IS NULL) OR
          (relation_kind = 'ambiguous' AND observed_json IS NOT NULL))
) WITHOUT ROWID;
CREATE TABLE validation_repair_edge_targets (
    snapshot_pk INTEGER NOT NULL,
    edge_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    target_node_pk INTEGER NOT NULL,
    PRIMARY KEY(snapshot_pk, edge_pk, position),
    UNIQUE(snapshot_pk, edge_pk, target_node_pk),
    FOREIGN KEY(snapshot_pk, edge_pk)
        REFERENCES validation_repair_edges(snapshot_pk, edge_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, target_node_pk)
        REFERENCES validation_repair_nodes(snapshot_pk, node_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_finding_nodes (
    snapshot_pk INTEGER NOT NULL,
    finding_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    role TEXT NOT NULL CHECK(role IN ('context', 'repair')),
    node_pk INTEGER NOT NULL,
    PRIMARY KEY(snapshot_pk, finding_pk, position),
    UNIQUE(snapshot_pk, finding_pk, node_pk),
    FOREIGN KEY(snapshot_pk, finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, node_pk)
        REFERENCES validation_repair_nodes(snapshot_pk, node_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batches (
    snapshot_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL CHECK(batch_pk >= 1),
    batch_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    focus_finding_pk INTEGER NOT NULL,
    finding_count INTEGER NOT NULL CHECK(finding_count >= 1),
    PRIMARY KEY(snapshot_pk, batch_pk),
    UNIQUE(snapshot_pk, batch_id),
    UNIQUE(snapshot_pk, position),
    FOREIGN KEY(snapshot_pk) REFERENCES validation_snapshots(snapshot_pk)
        ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, focus_finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk)
) WITHOUT ROWID;
CREATE TABLE validation_batch_rationale (
    snapshot_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    rationale TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, batch_pk, position),
    FOREIGN KEY(snapshot_pk, batch_pk)
        REFERENCES validation_batches(snapshot_pk, batch_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batch_findings (
    snapshot_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    finding_pk INTEGER NOT NULL,
    PRIMARY KEY(snapshot_pk, batch_pk, position),
    UNIQUE(snapshot_pk, finding_pk),
    FOREIGN KEY(snapshot_pk, batch_pk)
        REFERENCES validation_batches(snapshot_pk, batch_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, finding_pk)
        REFERENCES validation_findings(snapshot_pk, finding_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batch_repair_keys (
    snapshot_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    repair_key_pk INTEGER NOT NULL,
    PRIMARY KEY(snapshot_pk, batch_pk, position),
    UNIQUE(snapshot_pk, batch_pk, repair_key_pk),
    FOREIGN KEY(snapshot_pk, batch_pk)
        REFERENCES validation_batches(snapshot_pk, batch_pk) ON DELETE CASCADE,
    FOREIGN KEY(snapshot_pk, repair_key_pk)
        REFERENCES validation_repair_keys(snapshot_pk, repair_key_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE validation_batch_entries (
    snapshot_pk INTEGER NOT NULL,
    batch_pk INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('repair', 'context')),
    position INTEGER NOT NULL CHECK(position >= 0),
    entry TEXT NOT NULL,
    PRIMARY KEY(snapshot_pk, batch_pk, role, position),
    UNIQUE(snapshot_pk, batch_pk, role, entry),
    FOREIGN KEY(snapshot_pk, batch_pk)
        REFERENCES validation_batches(snapshot_pk, batch_pk) ON DELETE CASCADE
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS command_diagnostics (
    diagnostic_pk INTEGER PRIMARY KEY CHECK(diagnostic_pk >= 1),
    diagnostic_id TEXT UNIQUE NOT NULL,
    generation INTEGER UNIQUE NOT NULL CHECK(generation >= 1),
    operation TEXT NOT NULL,
    code TEXT NOT NULL,
    entry TEXT,
    summary TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS command_diagnostic_records (
    diagnostic_pk INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    kind TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY(diagnostic_pk, position),
    FOREIGN KEY(diagnostic_pk) REFERENCES command_diagnostics(diagnostic_pk)
        ON DELETE CASCADE
) WITHOUT ROWID;
CREATE INDEX validation_snapshots_slot_generation
    ON validation_snapshots(slot, generation DESC);
CREATE INDEX validation_snapshot_entries_lookup
    ON validation_snapshot_entries(snapshot_pk, entry, relation);
CREATE INDEX validation_findings_type_order
    ON validation_findings(snapshot_pk, type, position);
CREATE INDEX validation_findings_entry_order
    ON validation_findings(snapshot_pk, entry, position);
CREATE INDEX validation_findings_id
    ON validation_findings(snapshot_pk, finding_id);
CREATE INDEX validation_blocked_checks_order
    ON validation_blocked_checks(snapshot_pk, position);
CREATE INDEX validation_failed_checks_order
    ON validation_failed_checks(snapshot_pk, position);
CREATE INDEX validation_batches_order
    ON validation_batches(snapshot_pk, position);
CREATE INDEX validation_batches_id
    ON validation_batches(snapshot_pk, batch_id);
CREATE INDEX validation_batch_entries_lookup
    ON validation_batch_entries(snapshot_pk, entry, batch_pk, role);
CREATE INDEX validation_batch_findings_finding
    ON validation_batch_findings(snapshot_pk, finding_pk, batch_pk);
CREATE INDEX validation_finding_nodes_node
    ON validation_finding_nodes(snapshot_pk, node_pk, finding_pk);
CREATE INDEX validation_repair_edges_subject
    ON validation_repair_edges(snapshot_pk, subject_node_pk, edge_pk);
CREATE INDEX validation_repair_edge_targets_target
    ON validation_repair_edge_targets(snapshot_pk, target_node_pk, edge_pk);
CREATE INDEX IF NOT EXISTS command_diagnostics_generation
    ON command_diagnostics(generation DESC, diagnostic_pk);

PRAGMA user_version=19;
