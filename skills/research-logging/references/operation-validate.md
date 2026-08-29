# Validate Operation Instructions

Use this operation for independent mechanical validation of one maintained
research log. Run it as a validation agent, separate from the research agent
that changes research-owned material. Successful execution or inspection
during Record is not validation.

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
- `upgrade_required`: the target contains authored or generated metadata that
  must be upgraded to the current contract before evaluation; nothing was
  written;
- `incomplete`: a required mechanical observation was unavailable, so no new
  generated bundle was published.

The first three statuses exit zero because the requested evaluation or
preflight completed. `incomplete` exits nonzero. A tool failure also exits
nonzero and prints a precise error to standard error.

`upgrade.recovery.required` is a nonzero operational failure rather than a
mechanical finding. It means an interrupted metadata upgrade must be recovered
before validation can safely inspect or publish the target log.

## Report

Report the status, whether publication occurred, counts by mechanical scope and
status, and each non-passing check. Point the research agent to
`validation/mechanical.json` for machine-readable details and
`validation.md` for the human projection. Do not invent repair guidance; the
research agent applies the evidence specification and resolves the identified
condition in a separate task.

Mechanical validation does not continue into semantic review or reproduction.
Those are separate workflows with separate ownership and are not implemented
by this operation.
