# Reproduce Operation Instructions

Use this operation only when the researcher explicitly asks to reproduce a
maintained research log or entry. Reproduce mechanically plans from recorded
JSON authority, executes under confinement, compares regenerated outputs, and
publishes its own saved results. It does not judge scientific meaning or use
Markdown as execution authority.

Read `references/file-reproduction-records.md` before launching or reporting.
Read `references/file-entry-commands.md` when an admission failure or requested
repair requires interpreting authored command form or effective CID.

## Boundaries

- Keep research summaries, entries, scripts, registries, retained artifacts and
  prose read-only. Preview evaluates without publishing. Real launch publishes
  fresh completed validation under its normal locks before accepting work.
- After preparation, write only generated job/result/report paths and the
  existing eligible `requires_reproduction: true` to `false` effect in
  `pyrun.json`. Complete production and comparison are required; unequal
  artifacts remain complete production. Failed, partial, blocked or stopped
  work cannot clear early.
- Never repair recipes, change evidence rules or tolerances, select changed
  outputs for adoption, or promote automatically. Promotion needs separate
  researcher direction.
- Use whole-artifact exact comparison by default. Apply only an already-authored
  evidence-scoped exception; never infer one from changed results.
- Never add `--include-all` without explicit authorization for nonautomatic
  commands. Recheck alone does not authorize them.
- Do not copy the project into the workspace or edit generated records by hand.
  No validation runs automatically after reproduction publication.

## Preview Or Launch

Resolve the extensionless `scripts/log` entrypoint from this skill package.
Target one log or stable entry; multiple logs require separate commands.

```bash
<skill>/scripts/log reproduce plan --path <log> [--entry <entry>] \
  [--include-all] [--recheck] [--jobs <positive-integer>] \
  [--execution-timeout-seconds <seconds>] [--cursor <cursor>] [--format text|json]
<skill>/scripts/log reproduce run --path <log> [--entry <entry>] \
  [--include-all] [--recheck] [--jobs <positive-integer>] \
  [--execution-timeout-seconds <seconds>]
```

Plan is a bounded current-work view, not saved history or a launch token.
Present its text unchanged and follow returned cursors when more action rows
are needed. It creates no run, result, report or cache; ordinary lock
infrastructure is its only filesystem side effect. Launch prepares afresh rather
than consuming a previous preview.

Selection defaults to incremental. Not-needed precedes automatic policy;
unchanged previous failure/block is not retried. `--recheck` retries currently
eligible work without bypassing blockers or policy. Artifact matches do not
decide whether execution is needed. State which selection is used.

Keep useful partial plans: attributable source/input/boundary/baseline and
validation-admission causes block their owning work and dependents while
independent commands may run. Entry targets never schedule outside-entry
producers; other logs never become execution scope. Do not conflate
Reproduce-owned currentness with validation findings.

`--jobs` defaults to 1; accepted path conflicts, dependencies and project-wide
exclusivity can reduce concurrency. Review the plan before parallel launch.
The per-command wall-clock limit defaults to 300 seconds. Exceeding it records
`execution_timeout`, terminates the supervised tree and leaves independent work
eligible. Resume preserves both accepted settings. Current execution authority
is `research-log-pyrun/v6`; earlier schemas are unsupported.

Run prints a durable run ID and returns while the CLI-owned detached job
continues. A no-runnable-work invocation instead returns current reconciliation
and normally writes no job or saved result. Present that output immediately;
do not substitute an older saved summary.

For explicitly requested isolated verification after script/local-code repair,
use `log command verify --path LOG --entry ENTRY --cid CID --execution-id ID`.
It is synchronous, retains private diagnostics/outputs, preserves metadata and
results, and cannot resume, promote, clear requirements or adopt changed recipe
parameters/declarations or newly observed participating code.

## Observe Or Control A Run

```bash
<skill>/scripts/log reproduce status --path <log> --run-id <run-id>
<skill>/scripts/log reproduce status --path <log> --run-id <run-id> --json
<skill>/scripts/log reproduce stop --path <log> --run-id <run-id>
<skill>/scripts/log reproduce resume --path <log> --run-id <run-id>
```

Use text status for people and JSON for agents/monitors. Status is operational
lifecycle, not artifact outcome; queued work is not execution time. Stop is the
sole stopping action and preserves diagnostics and completed results.

Resume uses the same accepted Plan12/Job4, run ID, scope, authorization and
settings. It never replans or reruns durable terminal success/failure. Only
never-started or stopped nonterminal work may launch after safe cleanup.
Source changes require a new run. Publication-only recovery uses frozen facts
with no execution. Surviving workers preserve exclusion; never sweep unrelated
temporary paths. Old jobs are unsupported and remain unchanged; resolve them
under their owning implementation before incompatible cutover.

Run folders are
`<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>[-<entry>]-<run-id>/`.
The immutable UTC acceptance date organizes paths; lookup uses run ID alone.
Do not move or discard retained runs or derive a date selector.

Offer an optional scheduled monitor only after acceptance and create it only
after confirmation. It reports meaningful changes, completion/failure or user
action; it never stops, resumes or promotes.

## Present Saved Results Or Diagnose

After a broader run completes, retrieve the saved summary:

```bash
<skill>/scripts/log reproduce show --path <log> [--run-id <run-id>]
<skill>/scripts/log reproduce show --root <project>
```

Present the CLI-owned hierarchy or two root tables unchanged. Command and
artifact totals are different units. A dash is unavailable, not zero.
Saved inspection uses immutable selected-run facts, never current registries.
Omit run ID for the latest saved target; it is valid only with a single log.
No-work reconciliation is already the result of that invocation.

Use bounded list/detail for diagnosis, not SQLite, generated Markdown or a
complete diagnostic report:

```bash
<skill>/scripts/log reproduce list commands --path <log> [--run-id <run-id>] \
  [--entry <entry>] [--status <status>] [--reason <reason>] [--cursor <cursor>]
<skill>/scripts/log reproduce list artifacts --path <log> [--run-id <run-id>] \
  [--entry <entry>] [--cid <cid>] [--status <status>] [--reason <reason>] [--cursor <cursor>]
<skill>/scripts/log reproduce detail command --path <log> --entry <entry> \
  --cid <cid> --execution-id <id> [--run-id <run-id>] [--section <section>] [--cursor <cursor>]
<skill>/scripts/log reproduce detail artifact --path <log> --entry <entry> \
  --artifact <artifact> [--run-id <run-id>] [--section <section>] [--cursor <cursor>]
```

These routes also accept `--format text|json`. Use the list's exact detail
invocation, then section/cursor continuations for retained invocation, all
causes, output/comparison facts and available bounded stdout/stderr tails.
Missing/truncated diagnostics qualify availability, not saved outcome.
Never soften failed/blocked/not-matched/not-compared results. Evidence-scoped
matching does not mean whole-file equality.

`reproduction.md` contains only the same compact summary as single-log show.
Use `log reproduce render --path LOG` for report-only recovery without
replanning or execution. Unsupported results require separately authorized
`log reproduce run --path LOG --recheck`; never translate old results.
An explicit genuinely empty whole-log recheck may replace obsolete history with
an empty-confirmation receipt and report, with no fabricated run or execution.
Policy skips or blockers are not empty work.

Run Validate explicitly when current validation is required; later validation
does not rewrite historical reproduction outcomes or restore cleared flags.

## Explicit Promotion

Only with researcher direction:

```bash
<skill>/scripts/log reproduce promote --path <log> --run-id <run-id> \
  --cid <cid> --execution-id <execution-id>
```

Promotion copies the complete related native staged output set under existing
baseline, confinement, reservation and rollback guards. It leaves staged sources
and saved historical outcomes intact.
