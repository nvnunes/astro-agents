# Generated Reproduction Record Instructions

Use this file when Reproduce creates, reads, or publishes generated state.

## Ownership

Reproduce may create or update only these generated paths:

- `<log>/.cache/results.sqlite` and its safe SQLite companions;
- `<log>/reproduction.md`;
- `<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>-<run-id>/` for a log
  run, or
  `<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>-<entry>-<run-id>/`
  for an entry run, where the date is the UTC date of immutable
  `accepted_at`; and
- the existing operation-lock paths used to protect the selected log or entry
  and serialize reproduction publication; and
- `<project>/.cache/research-log-operations/reproduction-scheduler.sqlite` and
  its safe `-journal`, `-wal`, and `-shm` SQLite companions, the bounded
  normalized coordinator for active ordinary/exclusive permits, ordered path
  claims, and waiting exclusive tickets; and
- `<project>/.cache/research-log-operations/reproduction-scheduler.lock`, the
  existing-operation-lock mutex that serializes coordinator updates.

The reproduction domain of `.cache/results.sqlite` is the current local machine authority for
artifact outcomes, unchanged terminal failure and block dispositions,
per-run command accounting, and run history.
`reproduction.md` is its source-controlled human-only projection. Agents do not
parse either surface during ordinary work. Historical execution rows are
read-only; `log reproduce report --run-id` is unsupported. Status and command
queries may still accept run IDs. Use the summary/list report routes for
bounded status overviews:
`log reproduce report --path <log> --summary` for the compact per-log view,
`log reproduce report --root <project> --summary` for the cross-log view, and
the complete report or bounded artifact and command `list` and `show` routes
for complete comparison detail. Use command queries to enumerate the execution
units behind a compact command count; do not derive those lists by parsing this
generated record.

The result domain is disposable and rebuildable by reproduction. Removing it
discards local result history and unchanged failure and block dispositions, but
never touches a run-local `state.sqlite`, staged outputs, diagnostics, evidence
baselines, selection/fingerprint caches, or `pyrun.json`.
Current `pyrun.json` state still determines which successful commands do not
need reproduction. Rebuilding the discarded machine state requires an explicit
reproduction `--recheck`; an ordinary incremental invocation may correctly
select no commands. The committed Markdown report remains a human snapshot and
is never used to reconstruct machine state.

Any launch with no selected executions normally creates no run ID, run folder,
result write, or report write. The sole exception is an explicitly launched
empty whole-log `--recheck`, which may replace unsupported generated
reproduction results and `reproduction.md` after validation publication while
still creating no run or worker. Other no-work launches emit an ephemeral current
reconciliation using the plan's policy, no-work, and blocked selections together
with current artifact state. A prior completed run may be named only as
historical context; its command counts do not replace the current invocation's
counts.

Every newly published run in the current result-store schema counts each command
in its log or entry exactly once as
reproduction not needed, an unchanged prior failure, an unchanged prior block,
not automatic, succeeded, failed, or blocked by a planning condition or
selected command failure. Those command counts are separate from
artifact counts because one command may produce several artifacts.
Compact reports combine the first three internal categories into one
`reproduction not retried` total without exposing the prior disposition.
Each run also owns a complete immutable command-query projection containing
the recorded recipe and working directory, initial policy and queue state,
accepted selection, source digest, planning detail, accounting reason, and
terminal disposition. Historical command list and show queries use this
projection without consulting current `pyrun.json`.

Repair checks do not create command details or accepted runs. They preserve all
recorded observations and reproduction requirements and cannot be promoted.

Each evidence-relevant command has one current record keyed by entry, CID, and
execution ID. It stores a `succeeded`, `failed`, or `blocked` terminal
disposition and the exact digest of its recipe, environment, scripts, code,
inputs, dependency outputs, baselines, comparison definitions, and planning
state. Initial incremental reproduction uses current `pyrun.json` state
directly for completed commands and retains unchanged failure and block
dispositions. The accepted plan freezes one authorized queue for the run.
Resume does not replan, adopt source changes, reconsider failed or blocked
terminal outcomes, or add downstream work. It preserves every terminal
checkpoint and continues only never-started work or a checkpoint stopped before
a terminal outcome. A source or declaration change requires a new run. It never
infers this decision from artifact outcomes. `--recheck` is an initial-launch
override and does not apply to resume.

The reader accepts only the current result-store schema. An unsupported or
malformed store is not decoded, migrated, or used by incremental planning or a
partial publication retry. Command list and show do not reconstruct command rows
from a retained run directory, published aggregates, or current metadata. A
run accepted before a schema cutover cannot publish into an unsupported store.

For a current published command record, command show may supplement immutable
accounting with the matching retained run checkpoint and bounded stdout and
stderr tails. Those diagnostics are availability-qualified run-local evidence,
not fields reconstructed into the cumulative result. Removing the retained run
directory therefore removes diagnostic access without changing the published
command outcome.

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

New runs use run-local `state.sqlite` as durable job authority. It retains one
immutable accepted plan, the logical queue and `jobs` cap, active
entry/CID-qualified execution state, worker state, checkpoints, comparison context,
and publication-retry state. It has no attempt lineage. A
scheduling permit is released only after terminal checkpoint publication and
worker exit. Older job files remain untouched but are unsupported: Reproduce
does not resume, migrate, or decode them, and directs the caller to start a
new current-format run.

SQLite may create `state.sqlite-journal`, `state.sqlite-wal`, and
`state.sqlite-shm` beside the database. These safe companions share the run
store's ownership and count with `state.sqlite` toward the 256 MiB durable-store
limit whenever present.

## Research Boundary

Treat summaries, entries, scripts, commands, retained artifacts, and authored
registries as research-owned. Reproduce never edits them. After durably
recording a complete execution comparison, it may atomically clear only the
`pyrun`-owned reproduction requirement for that execution. It does not replace the
original execution observation. Reproduction publication never reads or writes
validation state; after it completes, ordinary validation runs separately and
owns its own result and report.

The maintained summary owns this stable navigation line:

```md
Reproduction: [latest report](<log>/reproduction.md)
```

Record initialization creates the link only; it creates no result database,
empty result rows, or placeholder report. Reorganize preserves or relocates it. Reproduce never changes
the summary line.

## Publication Boundary

A normally completed run publishes its complete requested artifact result set,
including failures and changes. A stop or operational publication failure does
not replace the prior authoritative result and does not restore reproduction
requirements already cleared for completed executions. A publication retry reuses durable
run state without rerunning terminal execution attempts. Generated reports
must expose every current non-matched and stale artifact; failures are never
hidden.

Do not edit generated records by hand. Do not use a reproduction agent to
promote run-local artifacts. Promotion is an explicit research mutation that
copies one complete execution output set under researcher direction.
The former `reproduction/results.json` location is unsupported after the
one-time path migration and is never read as a fallback.

## Current Run Records

Current runs use one immutable accepted plan inside `state.sqlite` plus typed
mutable job and checkpoint rows. Attempt histories, embedded duplicate plans,
and one-command plans are unsupported. Result projections may render historical
execution rows passively, but no current command creates or reports a
single-execution run.
