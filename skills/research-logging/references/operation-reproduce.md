# Reproduce Operation Instructions

Use this operation only when the researcher explicitly asks to reproduce a
maintained research log or one entry. Reproduce is a mechanical CLI workflow,
separate from Record, Review, and Validate. It starts from evidence declared in
`evidence.json`, plans and executes only from JSON authority, retains all
regenerated outputs in a project-local run folder, compares them with retained
artifacts, confirms complete matching executions immediately, and publishes
generated reproduction state. After successful reproduction publication, the
CLI invokes ordinary log validation as a separate operation.
It reads verified scripts, code, inputs, and comparison baselines in place;
it does not copy the project into that folder.

Read `references/file-reproduction-records.md` before launching or reporting a
run.

## Boundaries

- Treat the maintained summary, entries, commands, scripts, retained artifacts,
  `data.json`, `evidence.json`, `retention.json`, and authored prose as
  read-only. Reproduce writes its generated job, result, and report paths and
  may change only `confirmed: false` to `confirmed: true` for a fully matching
  execution in `pyrun.json`. The separate post-run validation owns its own
  generated files.
- Do not interpret Markdown as execution authority, select commands, repair a
  recipe, judge scientific meaning, or decide whether a changed artifact should
  replace retained research material.
- Do not edit research-facing prose or evidence presentation. The only human
  report owned by this operation is generated `reproduction.md`.
- Whole-artifact exact comparison remains the default. Reproduce may apply an
  authored evidence-scoped rule, but it never creates, changes, or guesses that
  rule or its evidence-level tolerance.
- Never add `--include-all` unless the researcher explicitly authorizes the
  non-automatic executions for that run. Omission is the normal default.
- Do not invoke promotion automatically. Promotion is a separate, explicit
  research mutation performed only with researcher direction.

## Preview Or Launch

Resolve the extensionless `scripts/log` entrypoint from this skill package.
Choose exactly one log or one entry; multiple logs require separate commands.

Preview a deterministic plan without creating a run ID, lock, directory,
checkpoint, result, report, or other state:

```bash
<skill>/scripts/log reproduce --path <log> [--entry <entry>] \
  [--include-all] [--recheck] [--jobs <positive-integer>] --dry-run
```

Launch the same scope by omitting `--dry-run`:

```bash
<skill>/scripts/log reproduce --path <log> [--entry <entry>] \
  [--include-all] [--recheck] [--jobs <positive-integer>]
```

Without `--entry`, the target is exactly the named log. With `--entry`, the
target is exactly that entry. Evidence dependencies outside the selected scope
remain boundaries; the CLI never widens the run by executing commands from
another entry or log.

`--jobs` defaults to `1`, preserving serial execution. A larger value is an
immutable per-run concurrency cap: dependency readiness, conflicting path
claims, and project-wide exclusivity may keep actual concurrency lower. Inspect
the dry-run's `jobs`, `exclusive`, and path-claim fields before a parallel
launch. Entry-local execution state must use `research-log-pyrun/v3`; earlier
schemas are unsupported.

The default selection is incremental: current results satisfy their artifact
cases, while new, unconfirmed, failed, stale, and dependency-affected eligible
executions are selected. When the researcher explicitly asks to recheck, check
again, or rerun already-current reproduction results, add `--recheck`. State
whether the preview or launch uses incremental or recheck selection.

The default run excludes executions with `auto_reproduce: false`.
`--include-all` includes them and requires separate explicit researcher
authorization. A request to recheck does not authorize non-automatic
execution. A real launch prints a run ID after
durable acceptance and returns immediately. The background job is CLI-owned
and does not depend on the launching agent or terminal remaining active.

## Observe Or Control A Run

Use the immutable run ID for every later action:

```bash
<skill>/scripts/log reproduce status --path <log> --run-id <run-id>
<skill>/scripts/log reproduce status --path <log> --run-id <run-id> --json
<skill>/scripts/log reproduce stop --path <log> --run-id <run-id>
<skill>/scripts/log reproduce resume --path <log> --run-id <run-id>
```

Use ordinary status for people. Agents and scheduled monitors use `--json` and
must not parse human text or generated files. `stop` is the sole stopping
action. It preserves diagnostics and completed checkpoints for an explicit
same-path `resume`; resume remains guarded by the original source snapshot.
The same command may retry a run whose sole operational failure was
reproduction-result publication; that retry reuses durable comparisons and
terminal attempts rather than rerunning commands.
Status reports every active execution and worker; queued work is not presented
as execution time. Resume always reuses the accepted `jobs` value and cannot
override it.

Accepted run folders live at
`<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>[-<entry>]-<run-id>/`,
where the date comes from immutable UTC `accepted_at`. All lifecycle commands
still use only `--run-id`; agents do not derive or supply the date.

A scheduled monitor is optional. Offer to create one only after a run is
accepted, and create it only after the user confirms. It should report
meaningful status changes, completion, failure, or required user action and
must never stop, resume, promote, or otherwise control the run.

## Report The Result

After completion, retrieve the centralized human projection:

```bash
<skill>/scripts/log reproduce report --path <log> [--entry <entry>]
```

Present the returned report unchanged. Never hide or soften `changed`,
`failed`, `comparison_failed`, `skipped`, or stale artifact results. Run status
describes operational completion and is independent of artifact outcomes.
Treat the subsequent validation outcome separately: its findings or failure do
not invalidate completed reproduction work.

An evidence-scoped artifact may report `matched` even when its complete file
fingerprint differs. In that case, use the bounded artifact `show` result when
the researcher needs the complete recorded per-evidence comparison. Do not
describe the whole files as equal.

For researcher-directed diagnosis, obtain bounded machine detail instead of
opening generated JSON:

```bash
<skill>/scripts/log reproduce artifacts list --path <log> [--entry <entry>] [--outcome <outcome>] [--artifact <path>]
<skill>/scripts/log reproduce artifacts show --path <log> --entry <entry> --artifact <path>
```

Do not select a changed result for adoption. A research agent acting with
researcher direction may inspect the retained complete execution output set
and then copy it into the log through:

```bash
<skill>/scripts/log reproduce promote --path <log> --run-id <run-id> --execution-id <execution-id>
```

Promotion copies every related output together and retains the run-local source.
It does not move or discard the run folder.
