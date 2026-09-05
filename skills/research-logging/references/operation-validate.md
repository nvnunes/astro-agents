# Validate Operation Instructions

Use this operation for independent mechanical validation of one or more
maintained research logs. Run Validate as a separate operation after Record.
It is read-only for research-owned material but normally writes generated
validation state; use `--dry-run` to publish no result or cache changes beyond
the generated coordination lock. The
same agent may invoke it, but while validating it must not edit or repair
research-owned material. A research-owned finding requires a later, separately
authorized Repair operation. `unsupported_metadata` is instead a
validation-state blocker: report its paths and stop. Before rerunning, ask the
user to authorize a separate action that archives the reported generated paths
outside the active log or removes them. Do not route that status to Record or
resolve it during Validate. Successful execution or inspection during Record
is not validation.

Read `references/file-validation-records.md` before invoking the canonical
tool.

## Boundaries

- Treat the maintained summary, entries, commands, scripts, artifacts,
  `data.json`, `retention.json`, evidence records, and prose as read-only.
- Write only the generated paths owned by
  `references/file-validation-records.md`.
- Validate the files on disk without consulting commits, branches, diffs, or
  other source-control state. The nearest Git worktree marker is used only to
  establish project scope for the shared generated cache.
- Do not execute research commands, inspect script internals for hidden
  associations, make semantic judgments, or attempt reproduction.
- Report findings precisely. Do not edit research content or generated records
  by hand.

Use the research project's required Python interpreter or launcher. Do not
silently substitute system Python when the project defines an environment with
the required artifact readers.

## Run

Resolve the extensionless `scripts/log` entrypoint from this skill package and
run one-log validation against the logical log path:

```bash
<skill>/scripts/log validate --path <log>
```

For repo-wide or multi-log validation, run the bounded all-log operation:

```bash
<skill>/scripts/log validate --root <project-root>
```

It uses the same canonical discovery contract as
`<skill>/scripts/log discover --root <project-root>` and returns one bounded
batch result. Its `report` field is the finished Markdown comparison for every
completed evaluation. Present that table unchanged; do not open generated
reports to reconstruct its counts. Report any `failures`, `incomplete`, or
`unsupported_metadata` results separately according to their status below.
Do not build the log set with filename globs, and do not exclude a candidate
because its basename is `validation.md`; discovery recognizes maintained
summaries by their stable navigation line and sibling log root, so generated
reports are not candidates.

Use `--date YYYY-MM-DD` only when the result date must be explicit. Use
`--dry-run` to evaluate without writing generated files. Use `--recompute` when
a cache-independent validation is required: it ignores both the per-log
check-comparison and selection cache and the project-level fingerprint cache,
evaluates every check from current research material, and rebuilds generated
cache state during a writable run. `--recompute --dry-run` performs the
complete fresh evaluation without writing generated files.

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
nonzero and prints a precise error to standard error. If a conflicting
research operation owns the log lock, stop and retry after it completes; do
not work around the lock or alter generated state.

## Report

Report according to the returned status:

- For `--root`, present the returned `report` table without recalculating or
  reformatting its cells. Then report any result omitted from that table using
  the applicable rule below.
- For a one-log `complete_clear` or `complete_findings` result, report whether
  publication
  occurred, counts by mechanical scope and status, and each non-passing check.
  Use the human report's unique-artifact counts for Provenance; the
  machine-readable scope aggregate remains a count of internal checks.
  When publication occurred, point any later repair operation to
  `validation/results.json` for machine-readable details and
  `validation.md` for the human projection. A dry run publishes neither file.
  A published CLI result is deliberately compact; read those generated files
  instead of expecting every check to be repeated on standard output. An
  unpublished dry run retains the complete record in its result because no
  generated bundle exists.
- For `unsupported_metadata`, report every path in `observed.paths` and state
  that no mechanical evaluation or generated file was published. Stop and
  request separate user authorization to archive or remove those generated
  paths. Do not point to `validation/results.json` or `validation.md` as the
  result of this invocation, and do not route the blocker to Record.
- For `incomplete`, report the unavailable required observations from the
  returned record and state that no new per-log generated bundle was
  published. A writable run may have retained completed project-level
  fingerprint observations and independently completed bounded selections;
  neither becomes a new comparison baseline. For a tool failure, report the
  precise operational error from standard error.

Do not invent item-specific repair guidance. A separately authorized Repair
operation resolves a reported research-owned condition from its exact target
and progressively loads only the applicable contract.

Mechanical validation does not continue into semantic review or reproduction.
Those are separate workflows with separate ownership and are not implemented
by this operation.
