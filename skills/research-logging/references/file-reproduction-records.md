# Generated Reproduction Record Instructions

Use this reference when creating, reading or publishing reproduction state.
The [reproduction specification](../../../docs/research-log-reproduction-spec.md)
owns exact records, versions, bounds, selectors and lifecycle contracts.

## Ownership

Reproduce owns its domain in `<log>/.cache/results.sqlite`, the compact
`<log>/reproduction.md`, accepted dated run directories, and its existing
operation/scheduler locks and coordinator. It does not own validation or command
diagnostics stored beside its result domain.

Use `log reproduce show`, `list commands|artifacts`, and
`detail command|artifact` to inspect immutable saved results. Optional
`--run-id` selects native historical results. Use `plan` for current work
without publishing or launching; saved queries do not reinterpret outcomes from
today's registries. Use `status` for an accepted job's operational lifecycle.
Do not parse generated SQLite or Markdown as an agent workflow.

Shared initialization creates version 19 without a reproduction domain.
Native publication installs version 22 lazily and atomically, replacing
unsupported reproduction tables without decoding or migrating their rows.
Unsupported reproduction reads are nonmutating and require explicit
`log reproduce run --path LOG --recheck`. No legacy reader or alias exists.

## Accepted Jobs And Saved Facts

New jobs use Job6 `state.sqlite` and immutable Plan14 work. Commands retain
typed recipes, inputs/outputs, roots, selection and dependencies; artifacts
retain producer/baseline/comparison bindings. Owned problems store each actual
diagnosis once; dependency and producer links describe effects without copied
per-output failures. Actual command and artifact results preserve observed
invocation, timings, partial outputs, comparison values and useful diagnosis.
Successful producer artifacts are compared before their exact consumers become
ready. A mismatch or unavailable comparison remains artifact-owned, is
referenced by only the blocked consumers, and does not abort independent work or
normal publication.

SavedRun14 is immutable per-run history with minimal latest identity indexes
for incremental retry/reuse. Command and artifact totals remain separate.
`reproduction.md` contains only the same compact hierarchy as single-log
`show`, prefixed by its heading; inventories and diagnostics belong to CLI
list/detail. Missing retained files qualify availability, not saved outcomes.

The result domain is disposable; clearing it also removes its report-materialization
marker, but leaves the physical report, jobs, research files, staged outputs,
diagnostics, validation and command domains unchanged.
Never reconstruct saved state from committed Markdown or retained historical
layouts. Rerun reproduction explicitly when replacement results are required.

## Research And Publication Boundaries

Do not edit generated records by hand. Preserve authored summaries, entries,
scripts, registries and retained baselines. Only acknowledged source
reconciliation may mutate an eligible execution after complete
production/comparison. It always clears the reproduction flag; an all-matched
comparison set also adopts the plan's accepted raw-script and effective-code
observations, including a null effective-code observation when current code is
unfingerprintable. Unequal or uncomputed results preserve prior source/output state.
Failed, partial, blocked or stopped work cannot reconcile.

Normal publication commits accepted/durable facts and the domain generation
atomically, then materializes the compact report. Frozen publication retry and
report-only `render` do not replan or execute. Stop and operational failure
remain job lifecycle state rather than replacing normally saved research results.

No runnable work normally creates no job, run or result/report write. Explicit
whole-log recheck with genuinely empty command/artifact work may replace obsolete
history with an empty-confirmation receipt, not a fabricated run. Supported or
absent history stays unchanged. Receipt recovery can finish an interrupted
report write; ordinary publication removes the receipt.

## Retained Runs

Runs are direct children of their immutable UTC acceptance-date directory:

```text
<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>-<run-id>/
<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>-<entry>-<run-id>/
```

Use run ID alone for lookup. Duplicate IDs are integrity failures; there is no
date selector, persistent run index or historical-path fallback. Preview and
read-only lookup create nothing. Keep every available regenerated output and
diagnostic in its original run-local location. No reproduction command discards,
relocates or supersedes these files.

Jobs and safe SQLite companions are durable operational state, not disposable
cache. Fixed-plan resume preserves accepted work and terminal results; source
changes require a new run. Grant release and scratch cleanup require exited
workers. Surviving workers keep exclusion active; recovery never sweeps unrelated
temporary paths or executes research work. Obsolete jobs are never decoded,
translated, or resumed; start a new explicit `--recheck` run instead.

Promotion is a separate researcher-directed mutation. It copies one complete
native staged output set and installs its accepted source observations under
existing baseline, confinement, reservation and rollback guards; it never moves
staged files or rewrites saved outcomes.
