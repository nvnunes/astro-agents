# Generated Reproduction Record Instructions

Use this file when Reproduce creates, reads, or publishes generated state.

## Ownership

Reproduce may create or update only these generated paths:

- `<log>/.cache/reproduction/results.json`;
- `<log>/reproduction.md`;
- `<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>-<run-id>/` for a log
  run, or
  `<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>-<entry>-<run-id>/`
  for an entry run, where the date is the UTC date of immutable
  `accepted_at`; and
- the existing operation-lock paths used to protect the selected log or entry
  and serialize reproduction publication; and
- `<project>/.cache/research-log-operations/reproduction-scheduler.json`, the
  bounded generated coordinator for active ordinary/exclusive permits and
  waiting exclusive tickets; and
- `<project>/.cache/research-log-operations/reproduction-scheduler.lock`, the
  existing-operation-lock mutex that serializes coordinator updates.

`.cache/reproduction/results.json` is the current local machine authority for
artifact outcomes, per-run command accounting, and run history.
`reproduction.md` is its source-controlled human-only projection. Agents do not
parse either file during ordinary work; use
`log reproduce report --path <log> --summary` for the compact per-log view,
`log reproduce report --root <project> --summary` for the cross-log view, and
the complete report or bounded artifact `list` and `show` routes for detail.

The machine record is disposable and rebuildable by reproduction. Removing it
discards local result history and saved-state reuse, so the next reproduction
is a cold run. The committed Markdown report remains a human snapshot and is
never used to reconstruct machine state.

The current result schema is `research-log-reproduction-result/3`. Every newly
published run counts every command in its log or entry target exactly once as
not automatic, reused from saved state, succeeded, failed, or blocked by a
selected command failure. Those command counts are separate from
artifact counts because one command may produce several artifacts. The reader
accepts canonical v2 results only so the next successful publication can write
v3; it does not reconstruct historical command counts.

Each run is a direct child of its acceptance-date directory. Reproduce resolves
existing runs by run ID alone through a bounded scan of those date directories;
it has no date argument, run index, or legacy lookup. A real acceptance creates
only the shared root, required date directory, and run directory. Dry runs and
read-only lookups create nothing.

The project `tmp` run folder contains the immutable run plan, lifecycle status,
logs, completed execution checkpoints, durable per-execution comparisons, and
every available regenerated output in its original workspace path. It is
diagnostic, non-authoritative research material. Reproduction does not delete,
relocate, or make a second copy of those outputs. A researcher may delete the
folder manually; later reporting prunes a run-history row only when absence can
be proved, and otherwise reports unknown availability.

New runs use the strict run/status v3 shapes. They retain the immutable `jobs`
cap, every active entry-qualified execution, complete worker history, and
per-attempt `active`, `succeeded`, `failed`, or `stopped` checkpoints. A
scheduling permit is released only after terminal checkpoint publication and
worker exit. Existing v2 runs remain readable and keep their original serial
record; they are never rewritten into v3.

## Research Boundary

Treat summaries, entries, scripts, commands, retained artifacts, and authored
registries as research-owned. Reproduce never edits them. After durably
recording a complete matching comparison, it may atomically update only the
`pyrun`-owned confirmation field for that execution. It does not replace the
original execution observation. Reproduction publication never reads or writes
validation state; after it completes, ordinary validation runs separately and
owns its own result and report.

The maintained summary owns this stable navigation line:

```md
Reproduction: [latest report](<log>/reproduction.md)
```

Record initialization creates the link and empty generated surfaces as one
transaction. Reorganize preserves or relocates them. Reproduce never changes
the summary line.

## Publication Boundary

A normally completed run publishes its complete requested artifact result set,
including failures and changes. A stop or operational publication failure does
not replace the prior authoritative result and does not roll back confirmations
already written for matching executions. A publication retry reuses durable
run state without rerunning terminal execution attempts. Generated reports
must expose every current non-matched and stale artifact; failures are never
hidden.

Do not edit generated records by hand. Do not use a reproduction agent to
promote run-local artifacts. Promotion is an explicit research mutation that
copies one complete execution output set under researcher direction.
The former `reproduction/results.json` location is unsupported after the
one-time path migration and is never read as a fallback.
