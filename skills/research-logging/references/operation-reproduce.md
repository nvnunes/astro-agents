# Reproduce Operation Instructions

Use this operation only when the researcher explicitly asks to reproduce a
maintained research log or one entry. Reproduce is a mechanical CLI workflow,
separate from Record, Review, and Validate. It starts from evidence declared in
`evidence.json`, plans and executes only from JSON authority, stages all
regenerated outputs in a disposable project-local run folder, compares them
with retained artifacts, and publishes generated reproduction state.

Read `references/file-reproduction-records.md` before launching or reporting a
run.

## Boundaries

- Treat the maintained summary, entries, commands, scripts, retained artifacts,
  `data.json`, `evidence.json`, `retention.json`, and authored prose as
  read-only. Reproduce writes only its generated job, result, and report paths.
- Do not interpret Markdown as execution authority, select commands, repair a
  recipe, judge scientific meaning, or decide whether a changed artifact should
  replace retained research material.
- Do not edit research-facing prose or evidence presentation. The only human
  report owned by this operation is generated `reproduction.md`.
- Never add `--include-slow` unless the researcher explicitly authorizes the
  slow executions for that run. Omission is the normal default.
- Do not invoke promotion automatically. Promotion is a separate, explicit
  research mutation performed only with researcher direction.

## Preview Or Launch

Resolve the extensionless `scripts/log` entrypoint from this skill package.
Choose exactly one log or one entry; multiple logs require separate commands.

Preview a deterministic plan without creating a run ID, lock, directory,
checkpoint, result, report, or other state:

```bash
<skill>/scripts/log reproduce --path <log> [--entry <entry>] [--include-slow] --dry-run
```

Launch the same scope by omitting `--dry-run`:

```bash
<skill>/scripts/log reproduce --path <log> [--entry <entry>] [--include-slow]
```

Without `--entry`, the target is exactly the named log. With `--entry`, the
target is exactly that entry. Evidence dependencies outside the selected scope
remain boundaries; the CLI never widens the run by executing commands from
another entry or log.

The default run skips executions recorded as slow. `--include-slow` includes
them and requires explicit researcher authorization. A real launch prints a run
ID after durable acceptance and returns immediately. The background job is
CLI-owned and does not depend on the launching agent or terminal remaining
active.

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

For researcher-directed diagnosis, obtain bounded machine detail instead of
opening generated JSON:

```bash
<skill>/scripts/log reproduce artifacts list --path <log> [--entry <entry>] [--outcome <outcome>] [--artifact <path>]
<skill>/scripts/log reproduce artifacts show --path <log> --entry <entry> --artifact <path>
```

Do not select a changed result for adoption. A research agent acting with
researcher direction may inspect the staged complete execution bundle and then
copy it into the log through:

```bash
<skill>/scripts/log reproduce promote --path <log> --run-id <run-id> --execution-id <execution-id>
```

Promotion copies every related output together and retains the staged source.
It does not move or discard the run folder.
