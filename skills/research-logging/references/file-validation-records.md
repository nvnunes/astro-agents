# Validation Record Instructions

Use this file for the ownership and lifecycle of generated research-log
validation records.

## Ownership

A validation agent may update only:

- `<log>/validation.md`;
- `<log>/validation-state.json`;
- `<log>/validation-index.json`;
- `<log>/validation-failures.md`;
- `<project>/.research-log-validation-index/manifest.json`;
- `<project>/.research-log-validation-index/incoming.json`.

It never edits a maintained summary, entry, script, retained evidence,
scientific artifact, authored `Validation:` note, `data.csv`, or `evidence.csv`.
Generate records through
`scripts/research_log_validation.py`; correct the scan or decisions rather than
hand-editing generated files.

## Records

`validation.md` is the human-readable source of truth for the last completed
validation. Its generated `## Status Summary` records the report-update date,
Summary status, and one status row per included entry. Detailed sections contain
one Summary row per presented summary statistic and one target table per entry.
The report gives counts and failures without assigning one overall log status.

`validation-state.json` is an agent-only incremental cache. It contains the
complete material-input fingerprint, completed successful and failed outcomes,
per-outcome dependency-identity snapshots and dependency contracts, selected
collection members, exact upstream material-to-producer bindings, orphan
dispositions, a minimum dependency graph slice, and the disposable resolutions
and current failure findings needed for reuse. Each graph slice fingerprints
the eligible upstream producer set as well as the selected invocation.
A reused outcome keeps its prior snapshot; changed dependencies reopen the
outcome instead of refreshing metadata around an old result. The file stores
metadata, not copies of research files. Missing, incompatible, or invalid
state causes complete rechecking; it is not a validation failure. The
validator serializes this machine-only record as deterministically ordered,
indented JSON so that an individual record change produces a bounded
source-control diff.

`validation-index.json` is the agent-only graph slice independently owned by
one log. It records typed nodes, edges, roots, and bounded mechanical or
reviewed origins. A research agent does not edit it. The validation agent
replaces it only through a complete canonical render. The slice deduplicates
fact origins into an identity-keyed table and uses deterministically ordered,
indented JSON. This keeps repeated provenance metadata small while preserving
line-level source-control review.

An ordinary validation run treats this log's slice as the item being replaced.
It may therefore recover from that slice being missing, stale, malformed, or
written under old rules, while requiring every other maintained log's slice to
remain current when cross-log reconciliation is requested. During first-time
initialization or a rules upgrade, validate logs sequentially. Mixed-version
slices make cross-log coverage incomplete, so orphan conclusions are withheld;
they do not invalidate otherwise truthful per-log records.
After every slice uses the current rules, run one complete reconciliation pass
over every maintained log before treating the rollout as finished.

The report, state, graph slice, and optional failure report form one canonical
bundle. A scan records that bundle's identity. Rendering publishes only when
the identity and complete scanned research-input snapshot are still current,
using the tool's current rules version. One repository advisory lock serializes
canonical validation operations. The tool stages and lints the bundle, then
atomically replaces each generated file. If interruption leaves a partial
bundle, the next run rejects it for reuse and rebuilds it; generated records are
not restored through a rollback journal.

`.research-log-validation-index/manifest.json` and `incoming.json` form the
small disposable repository aggregate used for cross-log orphan protection.
The consuming log owns each cross-log edge. Refresh the aggregate through the
validation tool from the per-log slices; never stage unrelated slices merely
to update one log. An incoming edge prevents the owning log from reporting
that path as orphaned but does not validate the consumer's evidence. Treat an
aggregate whose recorded graph identities differ from the current per-log
slices as stale and rebuild it before use.
If its two files are missing, inconsistent, mixed-version, or stale, discard
and rebuild the aggregate from compatible slices. It is not a
crash-recoverable source of truth and has no publication journal.

`validation-failures.md` is the generated current remediation queue. The
validator rebuilds it from current failures and deletes it after a clean
completed run. A research agent resolves or disputes a finding by changing
research-owned evidence or instructions; only a later validation run republishes
the queue.

The maintained summary contains only the fixed navigation line defined in
`skills/research-logging/references/file-summary-validation.md`. That link is
research-document scaffolding, not a validation result. Initialization installs
it once; every research operation preserves it, and validation never rewrites
it.

## Status Values

Use a local success date for a successful check, `FAIL` for a failed check,
`-` when reproducibility has no current result, and `N/A` only when a check is
not meaningful. Only `FAIL`, `-`, and `N/A` use inline code formatting in
rendered tables.

A standard run leaves Reproducibility at `-` or `N/A`. A correctly reported
failure is a completed validation result. Do not describe agent validation as
independent scientific replication.
