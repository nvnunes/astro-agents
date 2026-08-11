# Validation Record Instructions

Use this file for the ownership and lifecycle of generated research-log
validation records.

## Ownership

A validation agent may update only:

- `<log>/validation.md`;
- `<log>/validation-state.json`;
- `<log>/validation-failures.md`;
- `<project>/.research-log-validation-index.json`; and
- the maintained summary's `## Validation` section.

It never edits entries, scripts, retained evidence, scientific summary
content, `data.csv`, or `evidence.csv`. Generate records through
`scripts/research_log_validation.py`; correct the scan or decisions rather than
hand-editing generated files.

## Records

`validation.md` is the human-readable source of truth for the last completed
validation. It contains one Summary row per presented summary statistic and
one target table per entry. It reports row counts and failures without
assigning one overall log status.

`validation-state.json` is an agent-only incremental cache. It contains the
complete material-input fingerprint, completed successful and failed outcomes,
per-outcome dependency-identity snapshots and dependency contracts, selected
collection members, orphan dispositions, and the minimum disposable
resolutions and current failure findings needed for reuse. A reused outcome
keeps its prior snapshot; changed dependencies reopen the outcome instead of
refreshing metadata around an old result. The file stores metadata, not copies
of research files. Missing, incompatible, or invalid state causes complete
rechecking; it is not a validation failure.

`.research-log-validation-index.json` is the agent-only repository ownership
and reverse-dependency cache used for cross-log orphan reconciliation. It
contains file identities and dependency edges, not copies of research files.
Refresh it through the validation tool. An incoming edge prevents the owning
log from reporting that path as orphaned but does not validate the consumer's
evidence.

`validation-failures.md` is the current remediation queue. The validator
rebuilds it from current failures and deletes it after a clean completed run.
A research agent may clarify or remove findings it believes resolved but must
leave the file present with `None.` when the queue becomes empty. The research
agent then marks the affected summary projection STALE; only revalidation can
replace the last completed failed result.

The maintained summary's `## Validation` section is a projection of
`validation.md`. Generate it with the tool's `update-summary` operation after
a complete canonical render passes lint. Apply the staleness rules in
`skills/research-logging/references/file-summary-validation.md` during later
research-log maintenance.

## Status Values

Use a local success date for a successful check, `FAIL` for a failed check,
`-` when reproducibility has no current result, and `N/A` only when a check is
not meaningful. Only `FAIL`, `-`, and `N/A` use inline code formatting in
rendered tables.

A standard run leaves Reproducibility at `-` or `N/A`. A correctly reported
failure is a completed validation result. Do not describe agent validation as
independent scientific replication.
