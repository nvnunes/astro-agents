# Generated Validation Record Instructions

Use this file when mechanical validation reads research material and publishes
its generated result. Entry-root `pyrun-outputs.json` is separate `pyrun`-owned
support state; validation reads it but never writes or repairs it.

## Ownership

Mechanical Validate may create or update only these generated paths:

- `<log>/validation/mechanical.json`;
- `<log>/validation.md`;
- `<log>/.cache/research-log-validation.sqlite3` and its journal, WAL, and
  shared-memory companions;
- `<log>/.cache/research-log-operations/log.lock`; and
- `<project>/.cache/research-log-fingerprints.sqlite3` and its journal, WAL,
  and shared-memory companions.

`validation/mechanical.json` is the authoritative machine-readable result.
`validation.md` is its human projection and also carries the independent
Reproduction section. Mechanical Validate preserves any existing
`validation/reproduction.json`; reproduction is a separate operation.

The cache files are disposable generated acceleration state. The nearest
enclosing non-symlink Git worktree owns the project cache. Ignore every
`.cache/` directory in source control and research-log discovery. `--dry-run`
publishes no result or cache changes beyond the generated coordination lock,
while `--recompute` bypasses existing validation and fingerprint reuse for
that invocation.

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
stop and retry after that operation completes.

Do not edit generated records by hand. Report unsupported generated metadata
and request separate authorization before archiving it outside the active log
or removing it. Mechanical validation does not repair research material,
request agent judgment, execute research commands, perform semantic review, or
perform reproduction.
