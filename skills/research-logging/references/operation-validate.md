# Validate Operation Instructions

Use this operation for independent mechanical validation of one maintained
research log. Run Validate as a separate, read-only operation after Record. The
same agent may invoke it, but while validating it must not edit or repair
research-owned material. A research-owned finding requires a later, separately
authorized Record operation. `unsupported_metadata` is instead a
validation-state blocker: report its paths and stop. Before rerunning, ask the
user to authorize a separate action that archives the reported generated paths
outside the active log or removes them. Do not route that status to Record or
resolve it during Validate. Successful execution or inspection during Record
is not validation.

Read `references/file-validation-records.md` before invoking the canonical
tool.

## Boundaries

- Treat the maintained summary, entries, commands, scripts, artifacts,
  `data.csv`, evidence records, and prose as read-only.
- Write only the generated paths owned by
  `references/file-validation-records.md`.
- Validate the files on disk without consulting source-control state.
- Do not execute research commands, inspect script internals for hidden
  associations, make semantic judgments, or attempt reproduction.
- Report findings precisely. Do not edit research content or generated records
  by hand.

Use the research project's required Python interpreter or launcher. Do not
silently substitute system Python when the project defines an environment with
the required artifact readers.

## Run

Resolve `scripts/research_log_validation.py` from this skill package and run:

```bash
<project-python> <validation-tool> validate \
  --summary <log-summary>
```

Use `--date YYYY-MM-DD` only when the result date must be explicit. Use
`--jobs N` to change the positive worker bound. Use `--dry-run` to evaluate
without writing generated files. Use `--recompute` when a cache-independent
validation is required: it ignores the existing mechanical cache, evaluates
every check from current research material, and rebuilds the cache after a
completed published run. `--recompute --dry-run` performs the complete fresh
evaluation without writing generated files.

Interpret `status` as follows:

- `complete_clear`: the mechanical evaluation completed without findings;
- `complete_findings`: the mechanical evaluation completed and precisely
  identified one or more findings;
- `unsupported_metadata`: the target contains recognized unsupported generated
  metadata that prevents evaluation under the current contract; nothing was
  written;
- `incomplete`: a required mechanical observation was unavailable, so no new
  generated bundle was published.

The first three statuses exit zero because the requested evaluation or
preflight completed. `incomplete` exits nonzero. A tool failure also exits
nonzero and prints a precise error to standard error.

## Report

Report according to the returned status:

- For `complete_clear` or `complete_findings`, report whether publication
  occurred, counts by mechanical scope and status, and each non-passing check.
  When publication occurred, point any later repair operation to
  `validation/mechanical.json` for machine-readable details and
  `validation.md` for the human projection. A dry run publishes neither file.
- For `unsupported_metadata`, report every path in `observed.paths` and state
  that no mechanical evaluation or generated file was published. Stop and
  request separate user authorization to archive or remove those generated
  paths. Do not point to `validation/mechanical.json` or `validation.md` as the
  result of this invocation, and do not route the blocker to Record.
- For `incomplete`, report the unavailable required observations from the
  returned record and state that no new generated bundle was published. For a
  tool failure, report the precise operational error from standard error.

Do not invent item-specific repair guidance. A separately authorized Record
operation resolves a reported research-owned condition from the applicable
bundled research-logging instructions.

Mechanical validation does not continue into semantic review or reproduction.
Those are separate workflows with separate ownership and are not implemented
by this operation.
