# Validate Operation Instructions

Use this operation for independent mechanical validation of maintained research
logs and read-only diagnosis of saved validation findings or repair batches.
Validation reads research-owned material and normally publishes generated
validation state. It does not edit research material, execute research
commands, judge scientific meaning, or perform reproduction. A later Repair
operation must be separately authorized.

Read `references/file-validation-records.md` before invoking the canonical
tool.

## Boundaries

- Treat summaries, entries, commands, scripts, artifacts, registries, evidence
  records, retention declarations, and prose as read-only.
- Write only the generated paths owned by
  `references/file-validation-records.md`.
- Validate current files on disk without consulting source-control history.
- Report exact findings, blocked checks, and validator failures. Do not infer
  missing relationships or repair anything during Validate.
- Keep Reproduce currentness separate. A reproduction requirement or stale
  execution signature is not a validation finding or blocker.

Use the research project's required Python interpreter or launcher.

## Run

Resolve the extensionless `scripts/log` entrypoint from this skill package.

```bash
<skill>/scripts/log validate run --path <log>
<skill>/scripts/log validate run --path <log> --entry eNNN
<skill>/scripts/log validate run --root <project-root>
```

`--path` validates one logical log. `--entry` creates or replaces only that
entry's scoped snapshot. `--root` uses canonical bounded discovery and
validates every discovered log independently. Do not construct the log set
with filename globs.

Use `--dry-run` to evaluate without publishing snapshots, reports, or caches.
Use `--recompute-validation` to bypass evidence-selection reuse,
`--recompute-fingerprints` to bypass fingerprint reuse, or `--recompute` for
both.

Each applicable validation check has exactly one outcome:

- `pass`: the rule was evaluated and satisfied;
- `finding`: the rule identified an authored validation defect;
- `blocked`: the rule could not run because a finding or failed check blocked
  it; and
- `failed`: the validator could not complete that localized check reliably.

Rules excluded by applicability create no check and no count. Reproduction
currentness also creates no validation check.

A completed snapshot is `clear`, `findings`, or `failed`. Blocked checks do not
choose the snapshot outcome independently. Localized failed checks are saved,
independent checks continue, and their dependents are blocked. Capacity or
resource exhaustion, source mutation across the operation boundary, and other
whole-operation failures publish no new snapshot and preserve the prior one.

A one-log run exits 0 for `clear` or `findings`, 3 for `failed`, including a
nonpublishing dry run, and 2 for an operation failure. A nondry completed run
saves its snapshot. A root run uses precedence 2, then 3, then 0. If another
operation owns the log lock, report its supplied metadata
once and stop; do not retry, poll, or alter generated state.

## Saved Views And Report

Saved views never reevaluate research files:

```bash
<skill>/scripts/log validate show --path <log>
<skill>/scripts/log validate show --root <project-root>
<skill>/scripts/log validate list findings --path <log> [--entry eNNN] [--type TYPE]
<skill>/scripts/log validate list batches --path <log> [--entry eNNN]
<skill>/scripts/log validate list blocked --path <log> [--entry eNNN]
<skill>/scripts/log validate list failed --path <log> [--entry eNNN]
<skill>/scripts/log validate detail finding --path <log> [--entry eNNN] --id FINDING_ID
<skill>/scripts/log validate detail batch --path <log> [--entry eNNN] --id BATCH_ID
<skill>/scripts/log validate render --path <log>
```

Request `--format json` explicitly for structured output. Follow the exact
continuation command printed by a bounded text view.

The single-log summary reports finding counts under Conformance, Evidence,
Provenance, and Orphans, plus batches, blocked checks, and failed checks. The
cross-log table shows only finding-type and batch counts. It reports aggregate
Blocked and Failed counts below the table and marks those totals partial if a
discovered log lacks a readable full snapshot. Root text uses final directory
names and compact `Mon D` UTC dates; JSON retains canonical paths and exact
timestamps.

One finding check produces one finding at its natural subject.
Graph fan-out belongs in repair context, not the finding count. Every finding
belongs to one deterministic repair batch; batches may cross finding types.
Only residual singleton Orphans findings may be consolidated by exact entry
and orphan code.

`log validate render` rebuilds `validation.md` from the saved snapshot without
reevaluation. Never query or edit generated storage directly.

## Diagnose Named Findings

Use finding detail for one atomic defect and batch detail for a complete repair
packet. Finding detail includes the saved issue title and explanation, exact
rule and subject, source locations, containing batch, and complete bounded
diagnostic. For comparison defects, read its labelled reason, actual state,
and expected state before opening source files; inspect only the affected
research files and enough surrounding metadata to confirm or further explain
the deterministic defect.
Diagnosis is read-only and does not authorize a repair, semantic review, or
reproduction.

Entry selectors use stable physical entry IDs such as `e004`; split document
stems are not CLI entry selectors.
