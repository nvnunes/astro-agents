# Validate Operation Instructions

Use this operation for independent mechanical validation of one or more
maintained research logs. Run Validate as a separate, read-only operation after Record. The
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

Resolve `scripts/research_log_validation.py` from this skill package and run:

```bash
<project-python> <validation-tool> validate \
  --summary <log-summary>
```

For repo-wide or multi-log validation, first run canonical discovery:

```bash
<project-python> <validation-tool> discover --root <project-root>
```

Validate every path in the returned `summaries` array. Do not build that set
with filename globs, and do not exclude a candidate because its basename is
`validation.md`; discovery recognizes maintained summaries by their stable
navigation line and sibling log root, so generated reports are not candidates.

Use `--date YYYY-MM-DD` only when the result date must be explicit. Use
`--dry-run` to evaluate without writing generated files. Use `--recompute` when
a cache-independent validation is required: it ignores both the per-log
mechanical cache and the project-level fingerprint cache, evaluates every
check from current research material, and rebuilds generated cache state during
a writable run. `--recompute --dry-run` performs the complete fresh evaluation
without writing generated files.

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
  Use the human report's unique-artifact counts for Provenance; the
  machine-readable scope aggregate remains a count of internal checks.
  When publication occurred, point any later repair operation to
  `validation/mechanical.json` for machine-readable details and
  `validation.md` for the human projection. A dry run publishes neither file.
  A published CLI result is deliberately compact; read those generated files
  instead of expecting every check to be repeated on standard output. An
  unpublished dry run retains the complete record in its result because no
  generated bundle exists.
- For `unsupported_metadata`, report every path in `observed.paths` and state
  that no mechanical evaluation or generated file was published. Stop and
  request separate user authorization to archive or remove those generated
  paths. Do not point to `validation/mechanical.json` or `validation.md` as the
  result of this invocation, and do not route the blocker to Record.
- For `incomplete`, report the unavailable required observations from the
  returned record and state that no new per-log generated bundle was
  published. A writable run may have retained completed project-level
  fingerprint observations. For a tool failure, report the precise operational
  error from standard error.

When summarizing several completed logs in a Markdown table:

- Use separate `Research log`, `Conformance`, `Evidence`, `Provenance`,
  `Orphan findings`, and `Reports` columns with a valid Markdown header row.
- Introduce the table with: `Conformance, Evidence, and Provenance show Pass
  when all applicable checks pass; otherwise they are shown as
  passed/applicable. Orphan findings are unused-material artifact counts.
  Not-applicable checks are excluded from applicable denominators and reported
  separately.`
- Render Conformance and Evidence with one or more applicable checks as `Pass`
  when every applicable check passes. When either scope has a failing check,
  render it as `passed/applicable`, where `applicable = pass + fail`.
- Render Provenance from the unique-artifact row in `validation.md`, not from
  the number of provenance checks. Use `Pass` only when all counted artifacts
  pass and the scope status is `pass`; otherwise render
  `passed/applicable artifacts`. If the scope fails without a failed counted
  artifact, append ` (scope findings)` so command-level findings remain visible.
- Append `(+N N/A)` only when a scope with applicable checks contains `N`
  actual `not_applicable` checks. When a scope has no applicable checks and
  only `N` not-applicable checks, render it as `N N/A`. Exclude
  not-applicable checks from the applicable denominator.
- Render Orphan findings from the unique orphan-artifact row in
  `validation.md`. Do not add separately reported `orphan.input.unused`
  declarations to that artifact count. Render `0` when orphan classification
  ran without a material finding.
- Leave the scope cell blank when its total check count is zero. Do not render
  an empty scope as `0/0`, `NA`, `N/A`, or `not applicable`.
- Count `not_applicable` checks separately from failures in both per-log and
  aggregate totals.
- Describe the failure total as `failing checks` or `findings`, not
  `non-passing checks`, when `not_applicable` checks are excluded.

Use this shape:

```md
| Research log | Conformance | Evidence | Provenance | Orphan findings | Reports |
| --- | ---: | ---: | ---: | ---: | --- |
| Example findings | Pass | 3/5 | 4/5 artifacts | 7 | [Human](...) · [JSON](...) |
| Passing scopes | Pass | Pass | Pass (+2 N/A) | 0 | [Human](...) · [JSON](...) |
| No evidence checks | Pass |  |  | 0 | [Human](...) · [JSON](...) |
| Orphan classification not run | 0/1 | 0/1 |  |  | [Human](...) · [JSON](...) |
```

Do not invent item-specific repair guidance. A separately authorized Record
operation resolves a reported research-owned condition from the applicable
bundled research-logging instructions.

Mechanical validation does not continue into semantic review or reproduction.
Those are separate workflows with separate ownership and are not implemented
by this operation.
