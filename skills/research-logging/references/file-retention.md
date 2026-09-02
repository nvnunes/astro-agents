# Retention File Instructions

Use this file when intentionally retaining entry material that lies outside the
evidence-rooted command graph and would otherwise be orphaned.

Store declarations in optional entry-root `retention.json` with schema
`research-log-retention/v1`. It contains a non-empty `records` array and no
other top-level fields. Remove the file after removing its final record.

Use exact paths:

```json
{
  "schema": "research-log-retention/v1",
  "records": [{
    "id": "optimizer-debug-traces",
    "paths": ["data/debug-trace.json", "data/optimizer-state.npz"],
    "reason": "Diagnostic outputs retained for later investigation."
  }]
}
```

Or cover every eligible regular-file descendant of one exact directory:

```json
{
  "schema": "research-log-retention/v1",
  "records": [{
    "id": "intermediate-wavefronts",
    "directory": "data/intermediate-wavefronts",
    "membership": "all-descendants",
    "reason": "Intermediate states retained for later comparison."
  }]
}
```

IDs use lowercase letters, digits, and internal hyphens. Paths are normalized,
entry-relative, existing, non-symlink targets. Records must not overlap. A
directory declaration includes all descendants and must not be empty.

The optional `reason` records researcher or research-agent intent for later
semantic review. Mechanical validation does not interpret it.

Retention affects only orphan classification. It does not create evidence,
command, producer, input, lineage, or dependency relationships. Do not use it
to conceal missing metadata or provenance. A connected target makes the
declaration redundant and invalid; remove the declaration when the artifact
enters the evidence-rooted graph.

`evidence.json` contains presentation records only. Never place a retention
record there.
