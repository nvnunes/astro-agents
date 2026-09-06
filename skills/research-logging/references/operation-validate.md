# Validate Operation Instructions

Use this operation for independent mechanical validation of one or more
maintained research logs and for researcher-requested read-only diagnosis of
named mechanical-validation findings. Run Validate as a separate operation
after Record. A validation run is read-only for research-owned material but
normally writes generated validation state; use `--dry-run` to publish no
result or cache changes beyond the generated coordination lock. A diagnosis
does not rerun validation unless the researcher separately requests it. The
same agent may perform either path, but must not edit or repair research-owned
material. A research-log finding requires a later, separately authorized
Repair operation. `unsupported_metadata` is instead a validation-state
blocker: report its paths and stop. Before rerunning, ask the user to authorize
a separate action that archives the reported generated paths outside the
active log or removes them. Do not route that status to Record or resolve it
during Validate. Successful execution or inspection during Record is not
validation.

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
batch result. Its `report` field is the complete finished Markdown comparison
for every discovered log, including concise explanations for incomplete,
blocked, or operationally failed rows. Present it unchanged; do not open
generated reports or interpret the structured collections to reconstruct it.
Do not build the log set with filename globs, and do not exclude a candidate
because its basename is `validation.md`; discovery recognizes maintained
summaries by their stable navigation line and sibling log root, so generated
reports are not candidates.

Use `--date YYYY-MM-DD` only when the result date must be explicit. Use
`--dry-run` to evaluate without writing generated files. Use
`--recompute-validation` to bypass only the per-log check-comparison and
selection cache, or `--recompute-fingerprints` to bypass only the project-level
fingerprint cache. The two flags may be combined. `--recompute` remains
shorthand for both: it evaluates every check from current research material
and rebuilds both generated caches during a writable run. A dry run never
writes either cache; a bypassed cache is not opened for reuse.

Interpret `status` as follows:

- `complete_clear`: the mechanical evaluation completed without findings;
- `complete_findings`: the mechanical evaluation completed and precisely
  identified one or more findings;
- `unsupported_metadata`: the target contains recognized unsupported generated
  metadata that prevents evaluation under the current contract; nothing was
  written;
- `incomplete`: a required mechanical observation was unavailable, so no new
  generated bundle was published.

For one-log validation, the first three statuses exit zero because the
requested evaluation or preflight completed; `incomplete` exits nonzero. A
`--root` batch exits nonzero when `failures` is non-empty or any result is
`incomplete`, even though standard error can be empty. A top-level tool failure
that prevents a structured result also exits nonzero and prints a precise error
to standard error. If a conflicting research operation owns the log lock, stop
and retry after it completes; do not work around the lock or alter generated
state.

## Report

Present the returned `report` field unchanged for one-log and `--root`
validation. Do not reconstruct, reformat, supplement, or reconcile it against
the structured fields or generated files. The report already contains the
shared human area wording, publication links or `Not published`, and concise
explanations for incomplete or blocked results. The batch report likewise
includes every discovered log and every exceptional explanation even when the
command exits nonzero.

The other structured fields remain available to callers and establish exit
behavior, but are not an additional agent reporting task. For
`unsupported_metadata`, stop after presenting the report and request separate
user authorization before archiving or removing the identified generated
paths. Do not route the blocker to Record. When an invocation returns no
structured result at all, report the precise operational error from standard
error.

Do not invent item-specific repair guidance. A separately authorized Repair
operation resolves a reported condition from its exact target
and progressively loads only the applicable contract.

## Diagnose Named Findings

When the researcher asks to inspect, explain, triage, or determine the cause of
a named mechanical finding or bounded finding group, keep the work within
Validate and do not rerun validation unless requested.

For published findings, locate only the relevant bounded group:

```text
<skill>/scripts/log findings list --path <log> [--entry <entry>] [--subject <subject>]
```

Then retrieve each selected complete check needed to explain the shared cause:

```text
<skill>/scripts/log findings show --path <log> --id <check-id>
```

Use exact entry or subject filters and inspect only the affected research files
and enough surrounding metadata or recorded commands to explain the
deterministic failed relationship. Do not parse generated validation files,
inspect script internals, execute research commands, make semantic judgments,
or select Review lenses. Report the mechanical cause and affected scope in
plain language without Review finding classes or independence statements.

Diagnosis is read-only. Do not apply a correction or choose among plausible
repairs. Begin Repair only after the researcher explicitly asks to correct the
finding.

Mechanical validation does not continue into semantic review or reproduction.
Those are separate workflows with separate ownership and are not implemented
by this operation.
