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
- `<log>/validation.md`;
- `<project>/.cache/research-log-fingerprints.sqlite3`; and
- SQLite journal, WAL, and shared-memory companions for that project cache.

`validation/mechanical.json` is the authoritative machine-readable result. It
uses schema `research-log-mechanical/1` and records every mechanical check,
independent conformance, evidence, provenance, and orphan-scope aggregates, the
rules version, and the result date.

`validation/.cache/mechanical.json` is a disposable reuse projection. It uses
schema `research-log-mechanical-cache/6`, has an independent version history,
and may be removed or rebuilt without changing a validation conclusion. It
tracks compatible passing checks and project-relative evidence/script
observations. A rebuilt cache retains only observations used in that
evaluation; prior entries are reuse seeds, not persistent registry state. A
rules-version change invalidates cached checks.

`<project>/.cache/research-log-fingerprints.sqlite3` is the generated shared
filesystem-observation cache. SQLite schema version 1 stores canonical absolute
paths, strong content fingerprints, exact size and nanosecond modification and
change times, directory metadata identities, and directory membership. All
logs in one project share it. Unchanged files reuse their observed content
fingerprint. A changed directory reuses unchanged member-file fingerprints,
hashes only new or changed members, and reconstructs its aggregate fingerprint.
An `identity-files-sha256-v1` managed directory stores and reuses only its
bounded declared identity-file observations; it creates no recursive directory
membership state.
An `identity-patterns-sha256-v1` managed directory re-expands its bounded
selectors, then stores and reuses only the matched file observations. Added or
removed matches change the aggregate identity. Each wildcard parent is scanned
once per membership observation with a 100,000-candidate bound and without
descendant traversal.
The expected fingerprint in `data.json`, mechanical rules, and mechanical-cache
schema do not key or invalidate an otherwise current observation.

The nearest enclosing non-symlink `.git` file or directory owns the project
cache; directory names do not determine project scope. A read-only validation
treats corrupt, incomplete, locked, or unsupported project-cache state as
absent and continues with direct observation. A writable validation rebuilds
corrupt or incomplete generated state, but preserves and bypasses an
unsupported future schema. `--recompute --dry-run` does not open the database.

The project cache is generated acceleration state, not research identity or
execution history. A writable validation stores each completed file
observation transactionally, including observations completed before a later
incomplete result or interruption. `--dry-run` never creates or updates it.
`--recompute` bypasses both mechanical and fingerprint reuse. Ignore
`validation/.cache/` and the project `.cache/` directory in source control.

`validation.md` is the shared human-facing projection. Its Mechanical
Validation section shows the completion state and date, check counts for
Conformance and Evidence, unique starting-artifact counts for Provenance, and
unique orphan-artifact counts for Orphan. It collapses maximal all-orphan
directories for discussion while leaving artifact-level findings in
`mechanical.json`, reports unused input declarations separately, and groups
other non-passing checks by entry. Its independent Reproduction section shows
`not_yet_run` when no reproduction result exists. The report never combines
the two operations into one pass/fail conclusion and is never authoritative.

## Research Boundary

Treat maintained summaries, entries, scripts, artifacts, `data.json`,
`retention.json`, evidence
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
nonzero and publishes no new per-log bundle. A writable run may retain earlier
completed project-cache observations. A tool or publication failure also exits
nonzero. `--dry-run` evaluates and returns the result without acquiring the
publication lock or writing any generated file.

Canonical publication holds the per-log lock, rechecks the required observed
state, and replaces the generated files atomically per destination. An
ordinary publication error restores the prior completed bundle. Mechanical
cache absence, corruption, or an unsupported schema causes bounded
recomputation. Project-cache absence creates a new database; corruption
discards and rebuilds generated observations during a writable validation. An
unsupported future project-cache schema is preserved and bypassed. Compatible
project-cache schema changes require explicit migration. `--recompute` bypasses
all existing cache reuse for one invocation. A completed published
recomputation installs the newly rebuilt mechanical cache; a recomputation
combined with `--dry-run` leaves every generated path byte-identical.

Mechanical validation is code-only. It does not request agent judgment,
produce repair instructions, inspect script internals to infer associations,
execute research commands, perform semantic review, or perform reproduction.
