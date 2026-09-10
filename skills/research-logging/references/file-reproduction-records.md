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
artifact outcomes, unchanged terminal failure and block dispositions,
per-run command accounting, and run history.
`reproduction.md` is its source-controlled human-only projection. Agents do not
parse either file during ordinary work; use
`log reproduce report --path <log> --summary` for the compact per-log view,
`log reproduce report --root <project> --summary` for the cross-log view, and
the complete report or bounded artifact and command `list` and `show` routes
for detail. Use command queries to enumerate the execution units behind a
compact command count; do not derive those lists by parsing this generated
record.

The machine record is disposable and rebuildable by reproduction. Removing it
discards local result history and unchanged failure and block dispositions.
Current `pyrun.json` state still determines which successful commands do not
need reproduction. Rebuilding the discarded machine state requires an explicit
reproduction `--recheck`; an ordinary incremental invocation may correctly
select no commands. The committed Markdown report remains a human snapshot and
is never used to reconstruct machine state.

Any launch with no selected executions creates no run ID, run folder, result
write, or report write. It emits an ephemeral current
reconciliation using the plan's policy, no-work, and blocked selections together
with current artifact state. A prior completed run may be named only as
historical context; its command counts do not replace the current invocation's
counts.

The current result schema is `research-log-reproduction-result/8`. Every newly
published run counts every command in its log or entry target exactly once as
reproduction not needed, an unchanged prior failure, an unchanged prior block,
not automatic, succeeded, failed, or blocked by a planning condition or
selected command failure. Those command counts are separate from
artifact counts because one command may produce several artifacts.
Compact reports combine the first three internal categories into one
`reproduction not retried` total without exposing the prior disposition.
Each run also owns a complete immutable command-query projection containing
the recorded recipe and working directory, initial policy and queue state,
attempt selection, source digest, planning detail, accounting reason, and
terminal disposition. Historical command list and show queries use this
projection without consulting current `pyrun.json`.

Each evidence-relevant command also has one current record keyed by entry and
execution ID. It stores a `succeeded`, `failed`, or `blocked` terminal
disposition and the exact digest of its recipe, environment, scripts, code,
inputs, dependency outputs, baselines, comparison definitions, and planning
state. Initial incremental reproduction uses current `pyrun.json` state
directly for completed commands and retains unchanged failure and block
dispositions. A logical run's first attempt freezes its authorized queue.
Resume preserves successes, retries failures only after their source closure
changes, reconsiders blocks, reruns commands with no durable terminal outcome,
and adds only affected downstream commands already in that queue. It never
infers this decision from artifact outcomes. `--recheck` is an initial-launch
override and does not apply to resume.

The reader accepts only the current result schema. Any older generated result
is outdated and is not decoded, migrated, or used by incremental planning or a
partial publication retry. The CLI instructs the caller to launch whole-log
reproduction with `--recheck`; that complete plan may atomically replace the
outdated machine result and Markdown report. Command list and show do not
branch on concrete retired versions or reconstruct command rows from a retained
run directory, published aggregates, or current metadata. Malformed current
results remain invalid rather than being treated as outdated. The accepted
source snapshot records the result schema it may publish, so a run accepted
before a schema cutover cannot perform that replacement.

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

New runs use the strict run/status v4 shapes. They retain the immutable logical
queue and `jobs` cap, attempt lineage, every active entry-qualified execution,
complete worker history, and per-attempt `active`, `succeeded`, `failed`, or
`stopped` checkpoints. A
scheduling permit is released only after terminal checkpoint publication and
worker exit. Existing v2 and v3 runs remain readable under their original
compatibility paths and are never rewritten into v4.

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

Record initialization creates the link and empty generated surfaces as one
transaction. Reorganize preserves or relocates them. Reproduce never changes
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
