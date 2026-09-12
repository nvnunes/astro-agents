# Generated Validation Record Instructions

Use this file when mechanical validation reads research material and publishes
its generated result. Entry-root `pyrun.json` is separate `pyrun`-owned
execution state; validation reads it but never writes or repairs it.

## Ownership

Mechanical Validate may create or update only these generated paths:

- `<log>/.cache/results.sqlite` and its safe SQLite companions;
- `<log>/validation.md`;
- `<log>/.cache/research-log-validation.sqlite3` and its journal, WAL, and
  shared-memory companions;
- `<log>/.cache/research-log-operations/log.lock`; and
- `<project>/.cache/research-log-fingerprints.sqlite3` and its journal, WAL,
  and shared-memory companions.

The validation domain of `.cache/results.sqlite` is the current local
machine-readable authority. It retains the complete normalized validation
projection, including provenance chains and primary repair batches. It is
disposable; ordinary diagnosis and Repair use `log results` and `log findings`
and do not parse generated files directly. Missing current machine state
requires validation before queries.

`validation.md` is the concise, source-controlled human projection. Validate
and Repair do not parse it as machine authority. Reproduction is a separate
operation with its own domain in `results.sqlite` and `reproduction.md`;
mechanical validation preserves both.

The human report omits run dates and contains one compact Area and Result
table, and findings grouped by entry and human issue type. Each issue group
shows at most ten deterministic target details and an overflow command. It
contains no internal failure codes, check identities, raw observed state,
dependency mappings, passing totals, or repair instructions. A clear completed
result says `No mechanical findings.`

The validation domain keeps the latest full observation and the latest scoped
result for each stable entry. A new entry validation replaces only that entry's
result; a completed full validation replaces the full result and clears prior
scoped results. Entry validation preserves research-owned state.
Failed authoring commands may also retain the latest `diagnostic` snapshot per
log in this domain. Its rejected-command details are not validation evidence.
New diagnostics preserve full and entry results; full publication clears old
diagnostics. Use the printed text-inspection command for omitted details.
Inspection never evaluates research files. A result transaction failure preserves
the prior completed result; report rendering failure after commit leaves the
result queryable and is recoverable with `log results render --kind validation`.

All files below `.cache/` are disposable generated state. The nearest
enclosing non-symlink Git worktree owns the project cache. Ignore every
`.cache/` directory in source control and research-log discovery. `--dry-run`
publishes no result or cache changes beyond the generated coordination lock.
`--recompute-validation` bypasses per-log validation reuse,
`--recompute-fingerprints` bypasses project fingerprint reuse, and
`--recompute` remains shorthand for bypassing both during that invocation.

## Research Boundary

Treat maintained summaries, entries, scripts, artifacts, `data.json`,
`retention.json`, evidence records, and authored prose as research-owned.
Validation reads them but never edits them. Research operations preserve
generated validation files and do not hand-edit them.

The maintained summary owns this stable navigation line immediately below its
H1:

```md
Validation: [latest completed report](<log>/validation.md)
```

Validation never adds, removes, or rewrites this line.

## Publication Boundary

A writable completed evaluation publishes a coherent generated bundle while
holding the canonical log lock exclusively. Dry-run validation holds that same
lock for its complete read-only lifecycle. An incomplete evaluation or
publication failure does not replace the prior completed bundle. A dry run
publishes nothing. If another maintained operation owns a conflicting lock,
report its supplied owner metadata once and stop; do not retry or poll.

The result store is local cache state; `validation.md` is its derived human
summary. Removing validation rows does not alter the existing report, but it is
nonauthoritative and machine queries require validation to rebuild current
state. Reproduction planning has its own state and does not require the latest
validation result to remain present after admission. Former validation JSON,
batch JSON, and inspection-database locations are unsupported after cutover and
are never read as fallbacks.

Do not edit generated records by hand. Report unsupported generated metadata
and request separate authorization before archiving it outside the active log
or removing it. Mechanical validation does not repair research material,
request agent judgment, execute research commands, perform semantic review, or
perform reproduction.
