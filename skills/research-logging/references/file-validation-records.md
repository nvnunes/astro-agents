# Validation Record Instructions

Use this file for the ownership and lifecycle of generated research-log
validation records.

## Ownership

A validation agent may update only:

- `<log>/validation.md`;
- `<log>/validation/manifest.json`;
- immutable outcome, judgment, and failure shards referenced by that
  manifest; and
- rebuildable or transient state under `<log>/validation/.cache/`.

It never edits a maintained summary, entry, script, retained evidence,
scientific artifact, authored `Validation:` note, `data.csv`, or `evidence.csv`.
Generate records through `scripts/research_log_validation.py`; correct research
content through a separately authorized research operation rather than
hand-editing generated files.

## Persistence Classes

`validation.md` is the only researcher-facing validation record. It projects
the completed validation outcomes, their result dates, counts, failures, and
remediation for human readers. A later research change makes an affected
observation historical; it does not corrupt or erase the last completed
report.

`validation/manifest.json` is the small authoritative CLI-owned manifest. It
owns the current continuation, publication identity, and exact immutable row
shards under `validation/{outcomes,judgments,failures}/` that comprise durable
machine state. Those shards own semantic judgments and concise rationales,
completed outcomes, result dates, applicable rule dependencies, observed
evidence identities, and failures. The manifest may reference compatible
completed work while other work is incomplete. Accepted judgments and
outcomes have no competing machine-readable owner, and completion does not
recombine their shards into a monolithic record.

`validation/.cache/` is CLI-owned local state. `cache.json` may store reusable
file identities, hashes, inspections, and directory membership.
`subject-index.json` and `index-deltas/` map collision-checked stable subjects
to manifest-referenced row shards. `work/` owns active paged review files, and
`lock` serializes one log's writer. Missing, malformed, stale, or unwritable
local state changes no durable validation result and cannot make an uncertain
result succeed.

Commit `validation.md` and the complete durable `validation/` closure except
`validation/.cache/`. Ignore `**/validation/.cache/`.

Generated semantic packets and decision templates are temporary task files
under `validation/.cache/work/`. They are paired to one continuation and may
be regenerated from the durable record and current evidence. The agent edits
only the decision and rationale fields requested by the template.

## Durable Layout Contract

The manifest retains logical validation schema version 2. It contains the
project-relative `summary`, rule and completion dependencies, report result
and projection, continuation, `storage_layout`, `row_counts`, and exactly the
three shard collections `outcomes`, `judgments`, and `failures`. It contains
no cache, stable-subject-index, work-session, or lock descriptor.

Each shard reference contains only `kind`, `path`, `sha256`, `row_count`, and
`byte_count`. Its normalized POSIX path is relative to `validation/` and must
equal `<kind>/<sha256>.jsonl`. Reject absolute paths, traversal, backslashes,
symlinks, aliases, unexpected file types, duplicate identities, and any
path/content-identity disagreement.

The ignored stable-subject index contains its schema version, project-relative
summary identity, exact manifest row-shard closure identity, and
collision-checked subject mappings. Each accepted batch may add one ordered,
idempotent delta containing only that batch's mappings and closure transition.
The base plus deltas is usable only when it exactly reaches the current
manifest closure; otherwise rebuild the base deterministically from referenced
outcome and judgment shards. Compatibility checks still validate the loaded
rows' dependencies, rules, candidate content, and allowed answer.

Reused outcomes retain their original result dates. A current report may
therefore contain dates older than its report-update date: the current
operation established that the outcome's declared rule and evidence
dependencies remain compatible.

## Currentness And Publication

Validation is observational and outcome-specific. Each completed outcome owns
the exact evidence identities observed for that check. A later operation uses
stored size, modification time, and change time as its normal first check. If
all three are unchanged, it reuses the saved hash without opening content. If
metadata changed, it hashes the content once for all consumers and reopens
only outcomes whose content or applicable rules changed.

An explicitly reached cross-log path is external evidence of the consuming
log. The validator observes that path directly and never reads the external
log's validation files. Orphan status is local to the material presented by
the log being validated; another log's use does not exempt an unconnected
local file.

Missing, inaccessible, ambiguous, or changing evidence remains unresolved.
When a file changes during observation, the affected outcome is not completed
until a stable observation can be established. Unrelated research changes do
not discard compatible completed work.

Canonical writes take the stable per-log validation lock. The CLI validates
and atomically replaces only the target artifacts. New immutable shards are
published before `validation/manifest.json`; until the manifest is replaced,
the prior manifest remains authoritative. Update an ignored index delta only
after the manifest succeeds. A publication failure keeps the prior completed
`validation.md` available and retains any compatible newer progress already
committed through the manifest. Temporary replacement files and local runtime
state are generated mechanisms, not research inputs; exclude them from
discovery and orphan inventory.

The maintained summary contains only the fixed navigation line defined in
`skills/research-logging/references/file-summary-validation.md`. That link is
research-document scaffolding, not a validation result. Initialization installs
it once; every research operation preserves it, and validation never rewrites
it.

## Status Values

Use a local success date for a successful check, `FAIL` for a failed check, `-`
when reproducibility has no current result, and `N/A` only when a check is not
meaningful. Only `FAIL`, `-`, and `N/A` use inline code formatting in rendered
tables.

A standard run leaves Reproducibility at `-` or `N/A`. A correctly reported
failure is a completed validation result. Do not describe agent validation as
independent scientific replication.
