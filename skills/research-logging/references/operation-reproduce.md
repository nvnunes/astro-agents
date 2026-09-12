# Reproduce Operation Instructions

Use this operation only when the researcher explicitly asks to reproduce a
maintained research log or one entry. Reproduce is a
mechanical CLI workflow, separate from Record, Review, and Validate. It plans
and executes from JSON authority, retains regenerated outputs in a project-local
run folder, compares them with retained artifacts, and publishes generated
reproduction state. Completed executions clear their reproduction requirement.
After successful publication, the CLI
does not invoke validation. Run Validate explicitly when a current validation
result is required; reproduction does not copy the project into the run folder.

Read `references/file-reproduction-records.md` before launching or reporting a
run.

## Boundaries

- Treat the maintained summary, entries, commands, scripts, retained artifacts,
  `data.json`, `evidence.json`, `retention.json`, and authored prose as
  read-only. Reproduce writes its generated job, result, and report paths and
  may change only `requires_reproduction: true` to `requires_reproduction:
  false` for a completed execution in `pyrun.json`. A later explicit
  validation owns its own generated files.
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
Choose exactly one log or stable entry; multiple logs require separate commands.

Preview prepares one deterministic plan under the log lock without creating a
run ID, directory, checkpoint, result, report, cache entry, or other durable
state. The lock infrastructure is the preview's only filesystem side effect:

```bash
<skill>/scripts/log reproduce --path <log> [--entry <entry>] \
  [--include-all] [--recheck] [--jobs <positive-integer>] \
  [--execution-timeout-seconds <seconds>] --dry-run --summary
```

Use `--summary` for the bounded CLI-owned human projection. Present it
unchanged; do not request or parse the complete JSON plan. Programmatic
consumers that need the complete deterministic plan may omit `--summary`.

Launch the same scope by omitting both `--dry-run` and `--summary`:

```bash
<skill>/scripts/log reproduce --path <log> [--entry <entry>] \
  [--include-all] [--recheck] [--jobs <positive-integer>] \
  [--execution-timeout-seconds <seconds>]
```

Without `--entry`, the target is exactly the named log. With `--entry`, the
target is exactly that entry. Evidence dependencies outside the selected scope
remain boundaries; the CLI never widens the run by executing commands from
another entry or log.

For explicitly requested verification after a script or recorded local-code
repair, use `log repair-check --path LOG --entry ENTRY --execution-id ID`.
It is synchronous and isolated, retains private outputs and diagnostics, and
preserves `pyrun.json` and all generated records. It has no admission,
automatic-policy, run, resume, report, validation, publication, or promotion
lifecycle; its outputs cannot be promoted.
Do not use it to bypass changed recipe parameters/declarations or establish
new participating-code observations; those need a supported adoption route.

`--jobs` defaults to `1`, preserving serial execution. A larger value is an
immutable per-run concurrency cap: dependency readiness, conflicting path
claims, and project-wide exclusivity may keep actual concurrency lower. Before
a parallel launch, use the dry-run summary to verify the cap, runnable and
exclusive counts, and complete path claims. Entry-local execution state must
use `research-log-pyrun/v5`; earlier schemas are unsupported.

Each command defaults to a 300-second wall-clock runtime limit. Use
`--execution-timeout-seconds` on launch or dry run to accept a different limit;
resume retains it and does not accept an override. Exceeding the limit records
an `execution_timeout` failure, terminates the supervised process tree, and
does not prevent independent commands from running.

The default selection is incremental. A current execution with
`requires_reproduction: false` does not need execution and does not require
saved reproduction state. A command that still requires reproduction is
selected together with only the downstream commands its work may affect. Artifact matches are not
used to decide whether reproduction is needed. When the researcher explicitly
asks to recheck, check again, or rerun commands for which reproduction is not
needed, add `--recheck`. Recheck selects every
currently runnable eligible command but does not bypass a planning blocker.
State whether the preview or launch uses incremental or recheck selection.

Treat a valid partial plan as useful work. Fresh preparation evaluates the log
once under the log lock and derives admission from that evaluation, not from a
previously published validation bundle. A pre-existing changed or missing
script, participating code file, direct input, retained boundary, or comparison
baseline fails only its owning execution when the planner can identify it;
dependants are skipped and independent executions remain runnable. Do not
repair or reinterpret any of these findings during reproduction.

The default run first classifies a current execution with
`requires_reproduction: false` as reproduction not needed, even when
`auto_reproduce` is false. Its retained outputs bound traversal. The default
run then excludes remaining executions with `auto_reproduce: false`.
`--include-all` includes them and requires separate explicit researcher
authorization. A request to recheck does not authorize non-automatic
execution. When commands are selected for execution, a real launch prints a
run ID after durable acceptance and returns immediately. The background job is
CLI-owned and does not depend on the launching agent or terminal remaining
active. When a launch selects no executions, it instead prints the standard
current reconciliation immediately. Present that output unchanged: it is the
completed result of the invocation, not a planning failure, and it must not be
replaced with the latest completed run's historical command counts.

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
`resume`. Resume loads the same accepted plan and keeps its run ID, target,
include-all authorization, command inventory, and jobs cap. It never replans,
adopts source edits, or reruns a durable successful or failed command. It
launches only never-started work and work stopped without a durable terminal
outcome, after cleaning that invocation's incomplete generated outputs and
scratch. Source edits after acceptance require a new run; an unnoticed edit
can invalidate conclusions and requires explicit reassessment. A run whose
only failure is result publication may retry publication from durable terminal
evidence without executing a command. Status reports every active execution
and worker; queued work is not presented as execution time. Its JSON projection
reports whether the run is resumable. Resume always reuses accepted settings
and cannot accept selectors, `--jobs`, or `--recheck`.

Accepted run folders live at
`<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>[-<entry>]-<run-id>/`,
where the date comes from immutable UTC `accepted_at`. All lifecycle commands
still use only `--run-id`; agents do not derive or supply the date.

A scheduled monitor is optional. Offer to create one only after a run is
accepted, and create it only after the user confirms. It should report
meaningful status changes, completion, failure, or required user action and
must never stop, resume, promote, or otherwise control the run.

## Report The Result

For a broader launched run, immediately retrieve the centralized human projection:

```bash
<skill>/scripts/log reproduce report --path <log> --summary
```

Present the returned compact report unchanged. Its command tree relates total
commands to commands whose reproduction was not retried, policy-skipped
commands, and selected commands, then relates
selected commands to succeeded, failed, and blocked outcomes.
Its artifact tree relates current reachable artifacts to matched, not-matched, and
not-compared outcomes, including separate failed, blocked, comparison-failed,
and skipped reasons. Comparison-failed appears first and skipped appears last.
The totals are different units because one command may produce several
artifacts. Do not combine them or reconstruct either tree yourself.

Do not wait for the researcher to ask for this summary. If the launch itself
returned a no-execution reconciliation, present that CLI-owned summary
immediately instead; there is no run to observe and no follow-up report command
to substitute for it.

For a cross-log overview, use the CLI-owned aggregation:

```bash
<skill>/scripts/log reproduce report --root <project> --summary
```

Present its two tables and coverage line unchanged. A dash means the value is
unavailable, not zero. Use `--format json` with either summary route only for a
programmatic consumer. If the CLI reports an unsupported generated result
schema, launch whole-log reproduction with `--recheck`; do not resume a partial
publication or reconstruct historical counts.

If that whole-log target contains no recorded commands or artifact cases, the
launch still replaces unsupported generated results with empty state and a
not-yet-reproduced report. It creates no run and executes nothing. Preview is
read-only; omit `--dry-run --summary` to perform the recovery. A target with
policy-skipped commands or blockers is not empty. Do not enable nonautomatic
commands or delete research state to force this route.

When the researcher asks for every artifact, retained run, or entry-specific
detail, retrieve the complete report instead:

```bash
<skill>/scripts/log reproduce report --path <log> [--entry <entry>]
```

Never hide or soften `changed`, `failed`, `comparison_failed`, `skipped`, or
stale artifact results in that detail. Run status describes operational
completion and is independent of artifact outcomes. A later explicit validation
finding or failure does not invalidate completed reproduction work.

An evidence-scoped artifact may report `matched` even when its complete file
fingerprint differs. In that case, use the bounded artifact `show` result when
the researcher needs the complete recorded per-evidence comparison. Do not
describe the whole files as equal.

For researcher-directed diagnosis, obtain bounded machine detail instead of
opening generated JSON:

```bash
<skill>/scripts/log reproduce artifacts list --path <log> [--entry <entry>] [--outcome <outcome>] [--artifact <path>]
<skill>/scripts/log reproduce artifacts show --path <log> --entry <entry> --artifact <path>
<skill>/scripts/log reproduce commands list --path <log> [--bucket <bucket>] [--entry <entry>] [--reason <reason>] [--run-id <run-id>] [--format text|json]
<skill>/scripts/log reproduce commands show --path <log> --entry <entry> --execution-id <execution-id> [--run-id <run-id>] [--format text|json]
```

Use the command routes whenever the researcher asks which commands make up a
compact command count. The public list buckets are
`reproduction-not-retried`, `skipped-by-policy`, `succeeded`, `failed`, and
`blocked`. Omit `--run-id` for the latest completed run. Present text output
unchanged unless a programmatic consumer needs JSON. Completed-run queries use
the selected run's immutable historical records and never reinterpret them
through current `pyrun.json`. Failed-command listings include a concise error
type and message for triage and grouping without individual drill-down calls.
JSON rows expose the same summary in `error`, including its source and
truncation flag. Each text list row provides the exact
`commands show` invocation for that command. Use it to retrieve the retained
checkpoint failure, timing, observed outputs, diagnostic paths, and bounded
stderr and stdout tails. Command queries do not partially support a run whose
command-query schema is unsupported. If either route reports that state, run
reproduction with `--recheck` to rebuild the generated result; do not
reconstruct missing command identity or accounting from its run directory,
generated JSON, Markdown, terminal records, aggregate counts, or current
command metadata.

Do not select a changed result for adoption. A research agent acting with
researcher direction may inspect the retained complete execution output set
and then copy it into the log through:

```bash
<skill>/scripts/log reproduce promote --path <log> --run-id <run-id> --execution-id <execution-id>
```

Promotion copies every related output together and retains the run-local source.
It does not move or discard the run folder.

## Current Reproduction Boundary

`log reproduce` accepts only whole-log or stable-entry targets. It has no
one-command repair mode, and run-ID single-execution presentation is removed.
Each current run stores one immutable plan/9 and mutable checkpoint
state; stopped work resumes only that accepted plan. Use `log repair-check` for
one current invocation. Historical result/10 execution rows remain read-only.
