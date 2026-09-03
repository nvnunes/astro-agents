# Validate Operation Instructions

Use this operation for independent mechanical validation of one or more
maintained research logs. Run Validate as a separate operation after Record.
It is read-only for research-owned material but normally writes generated
validation state; use `--dry-run` for an entirely non-writing evaluation. The
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
  fingerprint observations and independently completed bounded selections;
  neither becomes a new comparison baseline. For a tool failure, report the
  precise operational error from standard error.

When summarizing several completed logs in a Markdown table:

- Use separate `Research log`, `Structure Failures`, `Evidence Failures`,
  `Provenance`, `Hygiene Issues`, and `Reports` columns with a valid
  Markdown header row.
- Treat Structure as the human-facing projection of machine scope
  `conformance`; do not rename the machine scope in generated JSON.
- Introduce the table with: `Structure Failures and Evidence Failures report
  failing mechanical checks. Provenance reports failed and unconfirmed unique
  starting artifacts. Hygiene Issues is the total number of orphan artifacts,
  unmatched outputs, and unused input declarations.`
- Render Structure Failures as `None` when the scope has applicable checks and
  none fail; otherwise render the integer failure count. Do not use a ratio:
  the Structure pass check is a clear-log sentinel rather than a coverage
  denominator.
- Render Evidence Failures as `None` when one or more applicable checks exist
  and none fail. When a check fails, render `failed/applicable`, where
  `applicable = pass + fail`.
- Render Provenance from the unique-artifact row in `validation.md`, not from
  the number of provenance checks. Render nonzero artifact states as
  `N failed` and `N unconfirmed`, joined by ` · ` when both occur. Use the
  human row's unavailable count for `unconfirmed`; do not call it unavailable
  or describe the remedy in the summary. Omit zero states and render `None`
  when neither state occurs. Do not use a ratio.
- An artifact whose `not_applicable` machine check depends transitively on an
  actual failed Provenance prerequisite is already projected as failed in the
  human artifact row. Preserve the authoritative machine check as
  `not_applicable`; do not count unconfirmed-output checks as actual failures
  when propagating this artifact outcome.
- Do not show other `not_applicable` checks in the multi-log summary. They
  remain explicit in `validation.md` and `mechanical.json`. Do not abbreviate
  `not_applicable` as N/A.
- If the Provenance scope fails without a failed counted artifact, append
  `scope findings` as another nonzero state so command-level findings remain
  visible.
- Render Hygiene Issues as the integer from its single finding-count row in
  `validation.md`. Do not reconstruct or deduplicate that number from
  individual machine checks. Render `0` when Hygiene evaluation ran without a
  finding.
- Leave the scope cell blank when its total check count is zero. Do not render
  an empty scope as `None`, `0/0`, `NA`, `N/A`, or `not applicable`.
- Keep `not_applicable` checks separate from failures in detailed and
  machine-readable results; exclude them from multi-log summary cells and
  aggregate finding totals.
- Describe the failure total as `failing checks` or `findings`, not
  `non-passing checks`, when `not_applicable` checks are excluded.

Use this shape:

```md
| Research log | Structure Failures | Evidence Failures | Provenance | Hygiene Issues | Reports |
| --- | ---: | ---: | ---: | ---: | --- |
| Example findings | 2 | 2/5 | 1 failed · 2 unconfirmed | 7 | [Human](...) · [JSON](...) |
| Passing scopes | None | None | None | 0 | [Human](...) · [JSON](...) |
| Awaiting confirmed runs | None | None | 2 unconfirmed | 0 | [Human](...) · [JSON](...) |
| Only unconfirmed provenance | None | None | 6 unconfirmed | 0 | [Human](...) · [JSON](...) |
| Scope-only provenance findings | None | None | scope findings | 0 | [Human](...) · [JSON](...) |
| No evidence checks | None |  |  | 0 | [Human](...) · [JSON](...) |
| Hygiene evaluation not run | 1 | 1/1 |  |  | [Human](...) · [JSON](...) |
```

Do not invent item-specific repair guidance. A separately authorized Record
operation resolves a reported research-owned condition from the applicable
bundled research-logging instructions.

Mechanical validation does not continue into semantic review or reproduction.
Those are separate workflows with separate ownership and are not implemented
by this operation.
