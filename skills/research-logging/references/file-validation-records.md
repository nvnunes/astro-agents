# Generated Validation Record Instructions

Use this file when mechanical validation reads research material and publishes
its generated result. The paths, statuses, and boundaries below are the
complete operating instructions for generated validation records.

## Ownership And Layout

A validation agent may write only these generated paths for the active
mechanical operation:

- `<log>/validation/mechanical.json`;
- `<log>/validation/.cache/mechanical.json`;
- `<log>/validation/.cache/lock`; and
- `<log>/validation.md`.

`validation/mechanical.json` is the authoritative machine-readable result. It
uses schema `research-log-mechanical/1` and records every mechanical check,
independent conformance, evidence, provenance, and orphan-scope aggregates, the
rules version, and the result date.

`validation/.cache/mechanical.json` is a disposable reuse projection. It uses
schema `research-log-mechanical-cache/2`, has an independent version history,
and may be removed or rebuilt without changing a validation conclusion. It may
reuse an artifact digest only when the project-relative regular file still has
the recorded byte size, modification time, and change time. `--recompute`
bypasses both check and artifact-identity reuse. A rules-version change
invalidates cached checks but not otherwise compatible artifact identities.
Ignore `validation/.cache/` in source control.

`validation.md` is the shared human-facing projection. Its Mechanical
Validation section shows the completion state and date, check counts for
Conformance, Evidence, and Orphan, unique starting-artifact counts for
Provenance, and every non-passing check grouped by entry. The Provenance row's
status remains the aggregate status of all provenance checks, including
command-level findings without an artifact. Passing check details remain in
`mechanical.json`. Its independent Reproduction section shows
`not_yet_run` when no reproduction result exists. The report never combines
the two operations into one pass/fail conclusion and is never authoritative.

## Research Boundary

Treat maintained summaries, entries, scripts, artifacts, `data.csv`, evidence
records, and authored prose as research-owned. Validation reads them but never
edits them. Research operations preserve generated validation files and do not
hand-edit them.

The maintained summary owns one stable navigation line immediately below its
H1:

```md
Validation: [latest completed report](<log>/validation.md)
```

Validation never adds, removes, or rewrites this line.

## Completion And Publication

`complete_clear` and `complete_findings` are completed mechanical evaluations.
A correctly identified finding is not a tool error. Both exit zero and publish
the generated bundle unless `--dry-run` is active.

`unsupported_metadata` is a completed preflight, not a mechanical evaluation.
It exits zero, lists the exact incompatible generated paths, and publishes
nothing. Validate reports the paths and stops. It does not remove them. Before
rerunning, archive them outside the active log or remove them through a
separately user-authorized maintenance action, not Record.

`incomplete` means at least one required observation was unavailable. It exits
nonzero and publishes no new bundle. A tool or publication failure also exits
nonzero. `--dry-run` evaluates and returns the result without acquiring the
publication lock or writing any generated file.

Canonical publication holds the per-log lock, rechecks the required observed
state, and replaces the generated files atomically per destination. An
ordinary publication error restores the prior completed bundle. Cache absence,
corruption, or an unsupported cache schema causes bounded recomputation rather
than changing the validation result. `--recompute` bypasses all existing cache
reuse for one invocation. A completed published recomputation installs the
newly rebuilt cache; a recomputation combined with `--dry-run` leaves the
existing generated bundle byte-identical.

Mechanical validation is code-only. It does not request agent judgment,
produce repair instructions, inspect script internals to infer associations,
execute research commands, perform semantic review, or perform reproduction.
