# Validation Record Instructions

Use this file for the ownership and lifecycle of generated research-log
validation records.

## Ownership

A validation agent may update only:

- `<log>/validation.md`;
- `<log>/validation-record.json`; and
- `<log>/validation-cache.json`.

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

`validation-record.json` is CLI-owned durable machine state. It owns semantic
judgments and concise rationales, completed outcomes, result dates, applicable
rule dependencies, observed evidence identities, failures, and progressive
continuation state. It may validly contain compatible completed work while
other work is incomplete. Accepted judgments and outcomes have no competing
machine-readable owner.

`validation-cache.json` is CLI-owned rebuildable acceleration data. It may
store reusable file identities, hashes, inspections, directory membership, and
local indexes. Missing, malformed, or stale cache data causes recomputation;
it cannot invalidate a readable report or make an uncertain result succeed.

Generated semantic packets and decision templates are temporary task files
outside the research log. They are paired to one continuation and may be
regenerated from the durable record and current evidence. The agent edits only
the decision and rationale fields requested by the template.

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
and atomically replaces only the target artifacts. A publication failure keeps
the prior completed `validation.md` available and retains any compatible newer
progress already committed to `validation-record.json`. Temporary replacement
files and the lock are generated mechanisms, not research inputs; exclude them
from discovery and orphan inventory.

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
