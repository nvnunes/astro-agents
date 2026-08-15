# Validation Record Instructions

Use this file for the ownership and lifecycle of generated research-log
validation records.

## Ownership

A validation agent may update only:

- `<log>/validation.md`;
- `<log>/validation-decisions.json`;
- `<log>/validation-state.json`; and
- `<log>/validation-index.json`.

It never edits a maintained summary, entry, script, retained evidence,
scientific artifact, authored `Validation:` note, `data.csv`, or `evidence.csv`.
Generate records through `scripts/research_log_validation.py`; correct the scan
or decisions rather than hand-editing generated files.

## Persistence Classes

`validation.md` is the durable human record of the last completed validation.
It records the exact local research snapshot, date, mode, rules, detailed
results, counts, failures, and remediation. A later research change makes the
report historical; it does not corrupt or erase the completed record.

`validation-decisions.json` is the durable machine-readable set of compatible
semantic judgments reachable from a report. Each judgment is content-addressed
and records its subject, complete available decision-input fingerprint, rules
version, result, date, rationale provenance, and recorded rationale when one
exists. Never invent reasoning omitted by an older format.

Each completed outcome and durable judgment declares its `rule_dependencies`
and typed `input_dependencies`. Producer-sensitive outcomes also declare a
native producer binding with a stable invocation identity and exact,
collection-scoped, or reviewed coverage. These fields are the compatibility
contract: a later scan reopens only consumers of a changed component or input
projection. Line numbers and other source locators may refresh without changing
the semantic identity.

`validation-state.json` and `validation-index.json` are disposable caches.
State accelerates local identity, inspection, completed-check, and orphan
comparison. The index is the owning log's graph slice for on-demand cross-log
views. Missing, malformed, stale, incompatible, or mixed-generation cache files
cause bounded recomputation or incomplete cross-log coverage; they do not
invalidate a readable report or compatible durable judgments.

Reused outcomes retain their original result dates. A current report may
therefore contain older dates without being stale: the current scan proved that
the outcome's declared rule and input dependencies remain compatible. During a
rolling upgrade, a report may also state that cross-log coverage is incomplete
while incompatible foreign slices are being replaced; its exact local results
remain valid.

Do not publish or depend on a repository aggregate. A scan discovers
maintained summaries and assembles an ephemeral view from source-current,
rules-compatible per-log slices. It lists contributing and excluded slices and
their reasons. An incoming cross-log use can remove an owned path from orphan
scope but does not validate the consumer's evidence. Local validation remains
exact when cross-log coverage is incomplete and reports that limitation.

Failures live in `validation.md` under `## Remediation`.
`validation-failures.md` is obsolete and is neither produced nor required.

## Currentness And Publication

The report header's `local_snapshot_identity` covers all validation-relevant
local inputs in the requested scope and excludes generated records, foreign
slices, and repository projections. Recompute and compare it to decide whether
a report is current. Report integrity, report currentness, decision
compatibility, and cache usability are separate diagnostics.

Read-only scan, prepare, review, lint, and audit work is lock-free. Canonical
writes take the stable per-log lock in the validation directory. Different logs
may publish concurrently; writers to the same log serialize. After acquiring
the lock, publication rechecks local currentness, atomically replaces durable
decisions and the report, and only then repairs disposable caches. A cache
write failure cannot roll back a completed durable publication.

Temporary replacement files and the per-log lock are generated mechanisms, not
research inputs. Exclude them from discovery, orphan inventory, and local
snapshot identities.

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
