# Generated Validation Record Instructions

Use this file when mechanical validation publishes its generated snapshot and
report. Entry-root `pyrun.json` is separate reproduction-owned execution state;
validation reads it but never writes or repairs it.

## Ownership

Mechanical Validate may create or update only:

- `<log>/.cache/results.sqlite` and its SQLite companions;
- `<log>/validation.md`;
- `<log>/.cache/research-log-validation.sqlite3` and its companions;
- `<log>/.cache/research-log-operations/log.lock`; and
- `<project>/.cache/research-log-fingerprints.sqlite3` and its companions.

The validation domain of `results.sqlite` is the current local machine
authority. It stores the latest completed full-log snapshot and the latest
scoped snapshot for each stable entry. A full snapshot replaces prior entry
snapshots. The normalized model contains finding rows, deterministic repair
batches, blocked checks with root blocker IDs, failed checks with bounded
diagnostics, and one shared repair-context graph. Passing checks and
applicability decisions are private evaluation data.

Each finding retains its exact rule, subject, bounded diagnostic values, and
source locations. The snapshot report context retains the stable issue title
and explanation for every saved finding code. `log validate detail finding`
combines those saved values directly; comparison defects record explicit
reason, actual state, and expected state when those distinctions identify the
violation.

The store does not expose command chains, admission effects, unresolved groups,
human issue groups, or generic stored-result identities. Ordinary diagnosis
and Repair use `log validate show`, `list`, and `detail`; never parse the
database directly.

`validation.md` is the concise source-controlled human projection of the same
saved snapshot. Beneath its heading it contains only the compact field/value
table shared with single-log `log validate show`: Log, Saved, Outcome,
Conformance, Evidence, Provenance, Orphans, Batches, Blocked, Failed. It contains
no inventories, diagnostics, navigation commands, or passing checks. Use `list`
and `detail` to inspect saved issues. Reproduction owns separate state and
`reproduction.md`; validation preserves both.

Command diagnostics occupy a separate command-owned domain. `log command show
--path LOG` reads the latest retained diagnostic. Command-diagnostic replacement
preserves validation and reproduction state, and validation replacement
preserves command diagnostics.

All `.cache/` state is disposable. Ignore it in source control and log
discovery. The nearest enclosing non-symlink Git worktree owns the project
fingerprint cache.

## Research Boundary

Treat maintained summaries, entries, scripts, artifacts, registries, evidence
records, retention declarations, and prose as research-owned. Validation reads
them but never edits them. The maintained summary owns this navigation line
immediately below its H1:

```md
Validation: [latest completed report](<log>/validation.md)
```

Validation never rewrites that line.

## Publication Boundary

A writable completed evaluation commits the snapshot in one atomic transaction
while holding the log lock, then atomically renders `validation.md`. A database
or report failure preserves the prior report bytes; a post-commit report failure
leaves the new snapshot queryable and marks the materialization stale for
`log validate render` recovery.

Localized failed checks belong to a completed `failed` snapshot and are
published. Whole-operation failure—including capacity exhaustion or a source
change across the operation boundary—publishes nothing and preserves the prior
snapshot. Dry runs publish nothing. Conflicting lock ownership is reported once
without retry or polling.

Store version 19 is a replacement schema. Versions 17 and 18 validation state are never
migrated or translated; the first successful writable validation replaces only
the validation domain while preserving reproduction and command-owned state.
Older generated validation formats are unsupported fallbacks.

Recognized obsolete generated-validation artifacts are ordinary Orphans
findings. Validation reports them without deleting or interpreting them.
