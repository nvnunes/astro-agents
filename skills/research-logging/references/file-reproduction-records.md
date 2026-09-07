# Generated Reproduction Record Instructions

Use this file when Reproduce creates, reads, or publishes generated state.

## Ownership

Reproduce may create or update only these generated paths:

- `<log>/reproduction/results.json`;
- `<log>/reproduction.md`;
- `<project>/tmp/reproduce-<log>-<run-id>/` for a log run, or
  `<project>/tmp/reproduce-<log>-<entry>-<run-id>/` for an entry run; and
- the existing operation-lock paths used to protect the selected log or entry
  and serialize reproduction publication.

`reproduction/results.json` is the cumulative machine authority for published
artifact outcomes and run history. `reproduction.md` is its human-only
projection. Agents do not parse either file during ordinary work; use `log
reproduce report` and the bounded artifact `list` and `show` routes.

The project `tmp` run folder contains the immutable run plan, lifecycle status,
logs, completed execution checkpoints, durable per-execution comparisons, and
every available regenerated output in its original workspace path. It is
diagnostic, non-authoritative research material. Reproduction does not delete,
relocate, or make a second copy of those outputs. A researcher may delete the
folder manually; later reporting prunes a run-history row only when absence can
be proved, and otherwise reports unknown availability.

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
