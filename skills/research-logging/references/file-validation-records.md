# Generated Validation Record Instructions

Use this file when mechanical validation reads research material and publishes
its generated result. The paths, statuses, and boundaries below are the
complete operating instructions for generated validation records. Entry-root
`pyrun-outputs.json` is separate `pyrun`-owned current support state; validation
reads it but never writes or repairs it.

## Ownership And Layout

A validation agent may write only these generated paths for the active
mechanical operation:

- `<log>/validation/mechanical.json`;
- `<log>/validation/reproduction.json` when reproduction publishes it;
- `<log>/validation.md`;
- `<log>/.cache/research-log-validation.sqlite3`;
- `<log>/.cache/research-log-validation.lock`;
- SQLite journal, WAL, and shared-memory companions for that per-log cache;
- `<project>/.cache/research-log-fingerprints.sqlite3`; and
- SQLite journal, WAL, and shared-memory companions for that project cache.

`validation/mechanical.json` is the authoritative machine-readable result. It
uses schema `research-log-mechanical/1` and records every mechanical check,
independent conformance, evidence, provenance, and internal orphan-scope
aggregates, the rules version, and the result date. The human report labels
that broader finding scope Hygiene; this does not introduce a new validation
class or alter the stable machine schema.

`<log>/.cache/research-log-validation.sqlite3` is disposable validation
acceleration state. SQLite schema version 1 contains independently versioned
`check_comparison` and `evidence_selections` components. Check comparison keeps
only passing dependency-bearing checks tied to the exact current authoritative
mechanical-report digest and current rules version. Selection reuse stores only
strict serialized successful `SelectionResult` values keyed by strong source
content identity, source profile, canonical locator identity, and locator
evaluator version. It never stores source payloads, parsed sources, open
handles, transformed presentations, or complete evidence checks.

One serialized selection may use at most 256 KiB and all retained selections
for one log may use at most 16 MiB. Oversized selections remain valid and are
simply recomputed later. Each completed stable selection is committed
independently. Only a completed published evaluation advances retention and
removes rows unused by that evaluation. Incomplete, interrupted, and read-only
runs do not remove prior rows or replace the check-comparison baseline.

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
The expected fingerprint in `data.json`, mechanical rules, and per-log
validation-cache schema do not key or invalidate an otherwise current
observation. Evidence source resolution reuses the verified registry
observation in memory; it does not hash the same file again before consulting
the selection cache.

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
`--recompute` bypasses check, selection, and fingerprint reuse. Ignore every
`.cache/` directory in source control and research-log discovery.

`validation.md` is the shared human-facing projection. Its Mechanical
Validation section shows the completion state and date, check counts for
Structure and Evidence, unique starting-artifact counts for Provenance, and
one total finding count for Hygiene. Structure projects machine scope
`conformance`; Hygiene projects machine scope `orphan`. These display labels
do not alter the stable machine schema. Orphan artifacts, unmatched output
records, and unused input declarations remain separately identified in
`mechanical.json`; the human table does not split their counts. An output
supported only by an unconfirmed reconstructed `pyrun` record is an
unavailable Provenance observation, not a failed Provenance check. The human
Provenance count reports those artifacts separately as unavailable; a
multi-log summary renders the count as `N unconfirmed`. It does not include
them in the failed artifact count. A downstream artifact whose
`not_applicable` check depends transitively on a confirmed Provenance failure
is instead a failed artifact in the human count; the authoritative dependent
check remains `not_applicable` in `mechanical.json`. Confirmed fingerprint or
lineage mismatches therefore propagate through the human artifact outcome
without duplicating machine failures. When failed and unconfirmed artifacts
both occur, the human Provenance row has failed aggregate status. Its
independent Reproduction section shows
`not_yet_run` when no reproduction result exists. The report never combines
the two operations into one pass/fail conclusion and is never authoritative.

## Research Boundary

Treat maintained summaries, entries, scripts, artifacts, `data.json`,
`retention.json`, evidence records, and authored prose as research-owned.
Validation reads them but never edits them. Research operations preserve
generated validation files and do not hand-edit them.

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
completed project-cache observations and bounded successful selections. A tool
or publication failure also exits nonzero. `--dry-run` evaluates and returns
the result without acquiring the publication lock or writing any generated
file.

Canonical publication holds the per-log lock, rechecks the required observed
state, and replaces the generated files atomically per destination. An
ordinary publication error restores the prior completed bundle. Mechanical
cache absence, corruption, rejected rows, or incompatible components cause
bounded recomputation. Writable validation rebuilds a corrupt per-log cache;
an unsupported future database or component version is preserved and
bypassed. Read-only validation opens existing cache state read-only and does
not create, update, or garbage-collect it. Project-cache absence creates a new
database; corruption discards and rebuilds generated observations during a
writable validation. An unsupported future project-cache schema is preserved
and bypassed. Compatible cache schema changes require explicit migration.
`--recompute` bypasses all existing cache reuse for one invocation. A completed
published recomputation repopulates the per-log cache; a recomputation combined
with `--dry-run` leaves every generated path byte-identical.

The writer lock is held before the writable per-log database opens and remains
held through evaluation, authoritative publication, comparison replacement,
and completed-run selection cleanup. The known legacy files
`validation/.cache/mechanical.json` and `validation/.cache/lock` are not read or
migrated. After a successful comparison-baseline rebuild they are removed, and
their directory is removed only when empty; unknown legacy-directory contents
are preserved.

Mechanical validation is code-only. It does not request agent judgment,
produce repair instructions, inspect script internals to infer associations,
execute research commands, perform semantic review, or perform reproduction.
