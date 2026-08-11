# Summary Validation Instructions

Use this file when creating or maintaining the summary's `## Validation`
projection.

Place `## Validation` immediately above `## AI Use`, or at the bottom when
`## AI Use` is absent. Include it in `## Contents`. Before the first canonical
validation run, use NOT RUN for applicable statuses and omit the report link
until `validation.md` exists.

When a canonical report exists, link to `<log>/validation.md`, then record the
summary-statistic status and one row per entry document:

```md
## Validation

[Detailed validation report](<log>/validation.md)

Summary statistics: <status>

| Scope | Last checked | Integrity & Provenance | Reproducibility |
| --- | --- | --- | --- |
| e001 | <status> | <status> | <status> |
```

Use these values:

- Summary statistics: `<date> — <total> checked; 0 failures`, `` `FAIL` -
  <failed> of <total> statistics failed``, NOT RUN, `N/A`, or STALE.
- Last checked: the most recent standard-check date, NOT RUN, or STALE.
- Integrity & Provenance: `<total> targets checked; 0 failures`, `` `FAIL` -
  <failed> of <total> targets failed``, NOT RUN, `N/A`, or STALE.
- Reproducibility: `<current> of <eligible> eligible targets reproduced`,
  `` `FAIL` - <failed> of <eligible> eligible targets failed``, `-`, `N/A`, or
  STALE.

Format only `FAIL`, `-`, and `N/A` as inline code in projection cells. Use
ordinary text for successful results, NOT RUN, and STALE.

Only the validation agent assigns completed results. A research agent preserves
them and applies staleness as follows:

- Changing presented evidence, its recorded workflow or dependencies, retained
  artifacts, its `evidence.csv` row, research-log-owned inventory, or an
  applicable entry `Validation:` instruction marks the affected scope STALE.
- Changing a presented summary statistic or its log-level `evidence.csv` row
  marks Summary statistics STALE.
- Changing qualitative prose, synthesis sections, prose sections, `## AI Use`,
  or the projection itself does not make validation stale.
- While an affected finding remains in `validation-failures.md`, leave its
  projection at `FAIL`. Change it to STALE only after the last corresponding
  working item is removed.

The research agent never edits `validation.md` or `validation-state.json`,
assigns a completed validation result, or deletes `validation-failures.md`.
The validation agent never edits research content or `evidence.csv`.
