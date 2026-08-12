# Summary Validation Instructions

Use this file when initializing the maintained summary's `## Validation`
section or publishing a completed canonical validation snapshot.

Place `## Validation` immediately above `## AI Use`, or at the bottom when
`## AI Use` is absent. Include it in `## Contents`.

## Before The First Validation

Initialize a new log with this fixed section:

```md
## Validation

Last validated on: NOT RUN

Summary statistics: NOT RUN
```

Do not add entry rows before validation. Later Record, Replace, Update Summary,
and Reorganize operations preserve this section byte-for-byte.

## Completed Snapshot

After a complete canonical render passes lint, use the validation tool's
`update-summary` operation to replace the complete section:

```md
## Validation

[Detailed validation report](<log>/validation.md)

Last validated on: <date>

Summary statistics: <status>

| Scope | Last checked | Integrity & Provenance | Reproducibility |
| --- | --- | --- | --- |
| e001 | <date> | <status> | <status> |
```

`Last validated on` is the report-update date of the displayed canonical
snapshot. Its rows identify only the entry documents included in that snapshot.
Later changes and entries do not alter the section or imply validation.

Use these values:

- Summary statistics: `<date> — <total> checked; 0 failures`, `` `FAIL` -
  <failed> of <total> statistics failed``, or `N/A`.
- Last checked: the displayed snapshot date.
- Integrity & Provenance: `<total> targets checked; 0 failures`, `` `FAIL` -
  <failed> of <total> targets failed``, or `N/A`.
- Reproducibility: `<current> of <eligible> eligible targets reproduced`,
  `` `FAIL` - <failed> of <eligible> eligible targets failed``, `-`, or `N/A`.

Format only `FAIL`, `-`, and `N/A` as inline code in generated cells. Use
ordinary text for successful results and dates.

## Ownership

Validate owns every completed snapshot. On an unchanged validation request,
run `update-summary` against the current canonical bundle after the fast scan
return; the operation is idempotent and replaces legacy summary formats or
`STALE` values without rerunning completed checks.

Record, Replace, Update Summary, and Reorganize preserve the existing section
byte-for-byte. They do not inspect the summary or generated validation records
solely to assess freshness, add rows for later entries, or assign `NOT RUN` or
`STALE`. The next Validate request detects changes from current inputs and
saved fingerprints, reuses only unchanged outcomes, and publishes a complete
new snapshot.
