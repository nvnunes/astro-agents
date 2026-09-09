# Generated Reproduction Record Instructions

Use this file when Reproduce creates, reads, or publishes generated state.

## Ownership

Reproduce may create or update only these generated paths:

- `<log>/reproduction/results.json`;
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

`reproduction/results.json` is the cumulative machine authority for published
artifact outcomes and run history. `reproduction.md` is its human-only
projection. Agents do not parse either file during ordinary work; use `log
reproduce report` and the bounded artifact `list` and `show` routes.

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
