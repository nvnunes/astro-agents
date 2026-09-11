# Reproduce Operation Instructions

Use this operation only when the researcher explicitly asks to reproduce a
maintained research log, one entry, or one recorded execution. Reproduce is a
mechanical CLI workflow, separate from Record, Review, and Validate. It plans
and executes from JSON authority, retains regenerated outputs in a project-local
run folder, compares them with retained artifacts, and publishes generated
reproduction state. Outside repair-verification mode, completed executions also
clear their reproduction requirement. After successful publication, the CLI
invokes ordinary log validation as a separate operation. It reads verified
scripts, code, inputs, and comparison baselines in place; it does not copy the
project into the run folder.

Read `references/file-reproduction-records.md` before launching or reporting a
run.

## Boundaries

- Treat the maintained summary, entries, commands, scripts, retained artifacts,
  `data.json`, `evidence.json`, `retention.json`, and authored prose as
  read-only. Reproduce writes its generated job, result, and report paths and
  may change only `requires_reproduction: true` to `requires_reproduction:
  false` for a completed execution in `pyrun.json`. The separate post-run
  validation owns its own
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
Choose exactly one log, entry, or entry-qualified execution; multiple logs require separate commands.

Preview a deterministic plan without creating a run ID, lock, directory,
checkpoint, result, report, or other state:

```bash
<skill>/scripts/log reproduce --path <log> [--entry <entry> [--execution-id <full-id>]] \
  [--include-all] [--recheck] [--jobs <positive-integer>] \
  [--execution-timeout-seconds <seconds>] --dry-run --summary
```

Use `--summary` for the bounded CLI-owned human projection. Present it
unchanged; do not request or parse the complete JSON plan. Programmatic
consumers that need the complete deterministic plan may omit `--summary`.

Launch the same scope by omitting both `--dry-run` and `--summary`:

```bash
<skill>/scripts/log reproduce --path <log> [--entry <entry> [--execution-id <full-id>]] \
  [--include-all] [--recheck] [--jobs <positive-integer>] \
  [--execution-timeout-seconds <seconds>]
```

Without `--entry`, the target is exactly the named log. With `--entry`, the
target is exactly that entry. Evidence dependencies outside the selected scope
remain boundaries; the CLI never widens the run by executing commands from
another entry or log.

For a requested individual rerun, add `--execution-id` with the full current
`pyrun-exec/v1:...` key from that entry's `pyrun.json`. Use completed-run command
queries for saved diagnostics; they do not inventory newly added recipes.
Inspect the preview's ID, script, complete output group, selection reason, and
all retained prerequisites and blockers. The command may have no evidence
references. Other producers, including same-entry prerequisites, remain verified
retained boundaries; do not widen scope when one is invalid. Use `--recheck`
for an authorized deliberate retry. It bypasses neither validation nor retained
prerequisite failures, and non-automatic execution still needs authorized
`--include-all`. A zero-execution preview does not verify execution. Resume
preserves the accepted single-command scope, and publication retains unrelated
results. Inspect command success and each artifact comparison separately.

For explicitly requested verification after a script or recorded local-code
repair, add `--verify-repair` alongside `--entry`, `--execution-id`, and
`--recheck`. Inspect the preview's recorded and accepted source fingerprints.
The option retains the recorded recipe and all prerequisite, baseline,
validation, and policy checks. Launch without `--dry-run --summary`, then use the
same status, resume, and command/artifact queries. Treat the command detail's
`repair_verification` marker as verification of repaired source, not adoption
of new ordinary execution history. This mode preserves `pyrun.json` completely,
including its reproduction requirement, and its outputs cannot be promoted.
Do not use it to bypass changed recipe parameters/declarations or establish
new participating-code observations; those need a supported adoption route.

`--jobs` defaults to `1`, preserving serial execution. A larger value is an
immutable per-run concurrency cap: dependency readiness, conflicting path
claims, and project-wide exclusivity may keep actual concurrency lower. Before
a parallel launch, use the dry-run summary to verify the cap, runnable and
exclusive counts, and complete path claims. Entry-local execution state must
use `research-log-pyrun/v4`; earlier schemas are unsupported.

Each command defaults to a 300-second wall-clock runtime limit. Use
`--execution-timeout-seconds` on launch or dry run to accept a different limit;
resume retains it and does not accept an override. Exceeding the limit records
an `execution_timeout` failure, terminates the supervised process tree, and
does not prevent independent commands from running.

The default selection is incremental. A current execution with
`requires_reproduction: false` does not need execution and does not require
saved reproduction state. An unchanged saved per-command source closure may
also preserve a terminal failure or block without repeating work that cannot
produce a different result. Machine state retains that prior failure or block;
the compact report includes the command in its single reproduction-not-retried
total. A command that still requires reproduction is
selected together with only the downstream commands its work may affect. Artifact matches are not
used to decide whether reproduction is needed. When the researcher explicitly
asks to recheck, check again, or rerun commands for which reproduction is not
needed, add `--recheck`. Recheck selects every
currently runnable eligible command but does not bypass a planning blocker.
State whether the preview or launch uses incremental or recheck selection.

Treat a valid partial plan as useful work. A pre-existing changed or missing
script, participating code file, direct input, retained boundary, or comparison
baseline fails only its owning execution when the planner can identify it;
dependants are skipped and independent executions remain runnable. Validation's
published admission effect similarly decides whether a finding affects no
execution, one chain, one physical entry, or the complete log. Do not repair or
reinterpret any of these findings during reproduction.

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
`resume`. Resume keeps the same logical run ID, original target, include-all
authorization, command queue, and jobs cap, but plans unresolved work against a
fresh attempt snapshot. It never reruns a succeeded command, reruns a failed
command only after its source closure changes, reconsiders blocked commands,
and reruns interrupted commands with no durable terminal checkpoint in clean
attempt-local output space. An unchanged failure produces the ordinary
zero-execution reconciliation. The same command may retry a run whose sole
operational failure was reproduction-result publication; that retry reuses
durable comparisons and terminal attempts rather than rerunning commands.
Status reports every active execution and worker; queued work is not presented
as execution time. Its JSON projection distinguishes scheduler completion from
logical `resolved` state and reports whether the run is `resumable`. Resume
always reuses the accepted `jobs` value and cannot override it. `--recheck`
applies only to the initial launch.

Accepted run folders live at
`<project>/tmp/reproduction/YYYY-MM-DD/reproduce-<log>[-<entry>]-<run-id>/`,
where the date comes from immutable UTC `accepted_at`. All lifecycle commands
still use only `--run-id`; agents do not derive or supply the date.

A scheduled monitor is optional. Offer to create one only after a run is
accepted, and create it only after the user confirms. It should report
meaningful status changes, completion, failure, or required user action and
must never stop, resume, promote, or otherwise control the run.

## Report The Result

For a single-execution run, immediately retrieve and present its short result:

```bash
<skill>/scripts/log reproduce report --path <log> --run-id <run-id>
```

This shows that execution's outcome and every output comparison, including
failure or block details. Human `status` uses the same view for individual runs.
Use it for both ordinary individual selection and repaired-source verification.
Do not present cumulative log artifact counts as the individual command result.
A blocked or otherwise empty selection already returns its short explanation;
present that response directly because no new run exists.

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

When the researcher asks for every artifact, retained run, or entry-specific
detail, retrieve the complete report instead:

```bash
<skill>/scripts/log reproduce report --path <log> [--entry <entry>]
```

Never hide or soften `changed`, `failed`, `comparison_failed`, `skipped`, or
stale artifact results in that detail. Run status describes operational
completion and is independent of artifact outcomes. Treat the subsequent
validation outcome separately: its findings or failure do not invalidate
completed reproduction work.

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
